import importlib
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone

from core.contracts.enums import RunState
from infra.storage.models import StoredArtifact
from infra.storage.models import StoredRun
from infra.storage.models import StoredRunEvent

import infra.storage.engine as storage_engine


def test_storage_models_hold_payloads() -> None:
    artifact = StoredArtifact(
        run_id="r1", key="k1", value={"x": 1}, updated_at_utc="2026-01-01T00:00:00Z"
    )
    event = StoredRunEvent(
        id="e1",
        run_id="r1",
        event_type="state_transition",
        payload={"to": "TRIAGED"},
        created_at_utc="2026-01-01T00:00:00Z",
    )
    run = StoredRun(
        run_id="r1",
        state=RunState.TRIAGED.value,
        run_type="ISSUE_TO_PR",
        payload={"repository": "acme/repo"},
        created_at_utc="2026-01-01T00:00:00Z",
        updated_at_utc="2026-01-01T00:00:01Z",
        artifacts=[artifact],
        events=[event],
    )
    assert run.artifacts[0].value["x"] == 1
    assert run.events[0].payload["to"] == "TRIAGED"


def test_storage_engine_caches_and_closes(monkeypatch) -> None:
    @dataclass
    class _FakeSyncEngine:
        disposed: bool = False

        def dispose(self) -> None:
            self.disposed = True

    class _FakeAsyncEngine:
        def __init__(self) -> None:
            self.disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    sync = _FakeSyncEngine()
    async_engine = _FakeAsyncEngine()

    class _FakeSettings:
        class database:
            url = "postgresql+psycopg://db"
            async_url = "postgresql+asyncpg://db"
            pool_size = 2
            max_overflow = 1
            echo = False

    monkeypatch.setattr(storage_engine, "get_settings", lambda: _FakeSettings, raising=False)
    monkeypatch.setattr(storage_engine, "create_engine", lambda *args, **kwargs: sync)
    monkeypatch.setattr(storage_engine, "create_async_engine", lambda *args, **kwargs: async_engine)
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    storage_engine._sync_engine = None
    storage_engine._async_engine = None
    assert storage_engine.get_engine() is sync
    assert storage_engine.get_engine() is sync
    assert storage_engine.get_async_engine() is async_engine
    assert storage_engine.get_async_engine() is async_engine
    storage_engine.close_engines()
    loop.close()
    asyncio.set_event_loop(None)
    assert sync.disposed is True
    assert async_engine.disposed is True
    assert storage_engine._sync_engine is None
    assert storage_engine._async_engine is None


def _load_review_requests_module():
    fake_schema = types.ModuleType("infra.storage.schema")

    class _Col:
        def __init__(self, name: str) -> None:
            self.name = name

        def __eq__(self, other):
            return (self.name, other)

    class _ReviewRequestsTable:
        c = types.SimpleNamespace(request_id=_Col("request_id"))

    fake_schema.review_requests = _ReviewRequestsTable()

    class _Conn:
        def __init__(self, executed: list) -> None:
            self.executed = executed

        def execute(self, query) -> None:
            self.executed.append(query)

    class _BeginCtx:
        def __init__(self, executed: list) -> None:
            self.executed = executed

        def __enter__(self):
            return _Conn(self.executed)

        def __exit__(self, exc_type, exc, tb):
            del exc_type
            del exc
            del tb
            return False

    class _FakeEngine:
        def __init__(self) -> None:
            self.executed: list = []

        def begin(self):
            return _BeginCtx(self.executed)

    fake_engine = _FakeEngine()
    fake_engine_module = types.ModuleType("infra.storage.engine")
    fake_engine_module.get_engine = lambda: fake_engine

    sys.modules["infra.storage.schema"] = fake_schema
    sys.modules["infra.storage.engine"] = fake_engine_module
    sys.modules.pop("infra.storage.review_requests", None)
    module = importlib.import_module("infra.storage.review_requests")
    return module, fake_engine


class _FakeQuery:
    def __init__(self):
        self.where_args = []
        self.values_kwargs = {}

    def where(self, *args):
        self.where_args.extend(args)
        return self

    def values(self, **kwargs):
        self.values_kwargs = kwargs
        return self


def test_review_requests_record_decision_and_mark_applied(monkeypatch) -> None:
    review_requests, fake_engine = _load_review_requests_module()
    updates: list[_FakeQuery] = []

    def fake_update(_table):
        query = _FakeQuery()
        updates.append(query)
        return query

    monkeypatch.setattr(review_requests, "update", fake_update)
    call_counter = {"count": 0}

    def fake_get_review_request(request_id: str):
        call_counter["count"] += 1
        if call_counter["count"] == 1:
            return {"request_id": request_id, "status": "pending", "decision": ""}
        return {"request_id": request_id, "status": "decided", "decision": "approved"}

    monkeypatch.setattr(review_requests, "get_review_request", fake_get_review_request)
    updated = review_requests.record_review_decision(
        request_id="rq-1",
        decision="approved",
        source="slack_button",
        decision_by="alice",
        reason="looks good",
    )
    assert updated["decision"] == "approved"
    assert updates[0].values_kwargs["status"] == "decided"
    assert updates[0].values_kwargs["decision_by"] == "alice"
    assert len(fake_engine.executed) == 1

    monkeypatch.setattr(
        review_requests,
        "get_review_request",
        lambda request_id: {"request_id": request_id, "status": "applied", "decision": "approved"},
    )
    applied = review_requests.mark_review_request_applied(
        request_id="rq-1", execution_run_id="run-2"
    )
    assert applied["status"] == "applied"
    assert updates[1].values_kwargs["status"] == "applied"
    assert updates[1].values_kwargs["execution_run_id"] == "run-2"
    assert len(fake_engine.executed) == 2


def test_review_requests_row_to_dict_defaults() -> None:
    review_requests, _fake_engine = _load_review_requests_module()

    class _Row:
        request_id = "rq-2"
        run_id = "run-1"
        run_type = "ISSUE_TO_PR"
        stage = "publish"
        stage_index = 4
        status = "pending"
        decision = None
        decision_source = None
        decision_by = None
        reason = None
        context = None
        slack_message_ref = None
        decided_at = None
        applied_at = None
        execution_run_id = None
        created_at = datetime.now(timezone.utc)
        updated_at = datetime.now(timezone.utc)

    row_dict = review_requests._row_to_dict(_Row())
    assert row_dict["decision"] == ""
    assert row_dict["context"] == {}
    assert row_dict["slack_message_ref"] == ""
