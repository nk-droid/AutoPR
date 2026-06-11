import logging
import os
import random
import threading
import time
import uuid
from collections import deque

logger = logging.getLogger("autopr.llm.ratelimit")

# Wait this long for a slot before giving up, then raise.
_WAIT_TIMEOUT_SEC = float(os.getenv("AUTOPR_LLM_RATE_WAIT_TIMEOUT_SEC", "30"))
# Poll interval while blocked (jittered to avoid thundering retries).
_POLL_SEC = float(os.getenv("AUTOPR_LLM_RATE_POLL_SEC", "0.05"))
# Concurrency lease: a permit held by a crashed worker is reclaimed after this.
# Should exceed the longest model call so live calls aren't reclaimed.
_LEASE_SEC = float(os.getenv("AUTOPR_LLM_RATE_LEASE_SEC", "900"))
_WINDOW_SEC = 60.0

class RateLimitTimeout(RuntimeError):
    """Raised when a slot could not be acquired within the wait timeout."""

def _wait_for(predicate, *, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise RateLimitTimeout(f"timed out waiting for {what}")
        time.sleep(_POLL_SEC * (1.0 + random.random()))

class _SlidingWindow:
    def __init__(self, limit: int, window_sec: float = _WINDOW_SEC) -> None:
        self.limit = limit
        self.window_sec = window_sec
        self.calls: deque[float] = deque()
        self._lock = threading.Lock()

    def try_admit(self) -> bool:
        with self._lock:
            now = time.monotonic()
            while self.calls and now - self.calls[0] > self.window_sec:
                self.calls.popleft()
            if len(self.calls) >= self.limit:
                return False
            self.calls.append(now)
            return True

class LocalLimiter:
    def __init__(self, *, rpm: int, max_concurrent: int) -> None:
        self._sem = threading.Semaphore(max_concurrent)
        self._window = _SlidingWindow(rpm)

    def acquire(self) -> str:
        if not self._sem.acquire(timeout=_WAIT_TIMEOUT_SEC):
            raise RateLimitTimeout("timed out waiting for concurrency slot")
        try:
            _wait_for(self._window.try_admit, timeout=_WAIT_TIMEOUT_SEC, what="rpm window")
        except Exception:
            self._sem.release()
            raise
        return "local"

    def release(self, token: str | None) -> None:
        if token:
            self._sem.release()

# Atomic "admit if window has room". Uses Redis server time to avoid clock skew.
_RPM_LUA = """
local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window)
if redis.call('ZCARD', KEYS[1]) >= limit then return 0 end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window)
return 1
"""

# Atomic "acquire a leased concurrency permit", reclaiming dead leases first.
_ACQUIRE_LUA = """
local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)
local lease = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - lease)
if redis.call('ZCARD', KEYS[1]) >= limit then return 0 end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], lease)
return 1
"""

class RedisLimiter:
    def __init__(self, client, *, key_prefix: str, rpm: int, max_concurrent: int) -> None:
        self._client = client
        self._rpm_key = f"{key_prefix}:rpm"
        self._conc_key = f"{key_prefix}:conc"
        self._rpm = rpm
        self._max_concurrent = max_concurrent
        self._rpm_script = client.register_script(_RPM_LUA)
        self._acquire_script = client.register_script(_ACQUIRE_LUA)

    def _run(self, script, key: str, *args) -> int | None:
        # Returns 1/0 from Lua, or None when Redis itself errors (fail open).
        try:
            return int(script(keys=[key], args=list(args)))
        except Exception:
            logger.warning("redis rate-limit op failed; failing open", exc_info=True)
            return None

    def acquire(self) -> str | None:
        token = str(uuid.uuid4())
        deadline = time.monotonic() + _WAIT_TIMEOUT_SEC

        # 1) concurrency permit (leased, released explicitly)
        while True:
            admitted = self._run(
                self._acquire_script, self._conc_key,
                int(_LEASE_SEC * 1000), self._max_concurrent, token,
            )
            if admitted is None:
                return None  # Redis down -> fail open, nothing to release
            if admitted == 1:
                break
            if time.monotonic() >= deadline:
                raise RateLimitTimeout(f"timed out waiting for concurrency slot ({self._conc_key})")
            time.sleep(_POLL_SEC * (1.0 + random.random()))

        # 2) RPM window (rate, no release)
        while True:
            admitted = self._run(
                self._rpm_script, self._rpm_key,
                int(_WINDOW_SEC * 1000), self._rpm, str(uuid.uuid4()),
            )
            if admitted is None:
                self.release(token)  # Redis blip mid-acquire -> free the slot, fail open
                return None
            if admitted == 1:
                return token
            if time.monotonic() >= deadline:
                self.release(token)
                raise RateLimitTimeout(f"timed out waiting for rpm window ({self._rpm_key})")
            time.sleep(_POLL_SEC * (1.0 + random.random()))

    def release(self, token: str | None) -> None:
        if not token:
            return
        try:
            self._client.zrem(self._conc_key, token)
        except Exception:
            logger.warning("redis rate-limit release failed for %s", self._conc_key, exc_info=True)

_redis_client = None
_redis_lock = threading.Lock()

def _get_redis_client():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    url = os.getenv("AUTOPR_REDIS_URL")
    if not url:
        return None
    with _redis_lock:
        if _redis_client is None:
            try:
                import redis as redis_sync
                _redis_client = redis_sync.from_url(url, decode_responses=True)
            except Exception:
                logger.warning("could not init redis for global rate limiting; using per-worker limits", exc_info=True)
                _redis_client = None
    return _redis_client

def build_limiter(*, key_prefix: str, rpm: int, max_concurrent: int):
    """Global (Redis) limiter when AUTOPR_REDIS_URL is set, else per-worker."""
    client = _get_redis_client()
    if client is None:
        return LocalLimiter(rpm=rpm, max_concurrent=max_concurrent)
    return RedisLimiter(client, key_prefix=key_prefix, rpm=rpm, max_concurrent=max_concurrent)
