import pytest

import infra.llm.rate_limit as rl
from infra.llm.rate_limit import LocalLimiter, RateLimitTimeout, build_limiter


@pytest.fixture(autouse=True)
def _fast_timeouts(monkeypatch):
    # Keep the blocking-wait paths fast so saturation tests don't hang.
    monkeypatch.setattr(rl, "_WAIT_TIMEOUT_SEC", 0.15)
    monkeypatch.setattr(rl, "_POLL_SEC", 0.01)


def test_local_limiter_admits_within_limits() -> None:
    limiter = LocalLimiter(rpm=10, max_concurrent=2)
    a = limiter.acquire()
    b = limiter.acquire()
    assert a == "local" and b == "local"
    limiter.release(a)
    limiter.release(b)


def test_local_limiter_times_out_when_concurrency_full() -> None:
    limiter = LocalLimiter(rpm=100, max_concurrent=1)
    held = limiter.acquire()
    with pytest.raises(RateLimitTimeout):
        limiter.acquire()
    limiter.release(held)
    # slot freed -> acquire works again
    assert limiter.acquire() == "local"


def test_local_limiter_times_out_when_rpm_exhausted() -> None:
    limiter = LocalLimiter(rpm=1, max_concurrent=10)
    limiter.acquire()  # consumes the only token in the window
    with pytest.raises(RateLimitTimeout):
        limiter.acquire()


def test_release_none_is_safe() -> None:
    LocalLimiter(rpm=1, max_concurrent=1).release(None)  # no error


def test_build_limiter_falls_back_to_local_without_redis(monkeypatch) -> None:
    monkeypatch.delenv("AUTOPR_REDIS_URL", raising=False)
    monkeypatch.setattr(rl, "_redis_client", None)
    limiter = build_limiter(key_prefix="autopr:llm:test", rpm=5, max_concurrent=5)
    assert isinstance(limiter, LocalLimiter)
