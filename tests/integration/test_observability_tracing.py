import pytest

import observability.tracing as tracing

def test_resolve_attributes_with_mapping_and_callable() -> None:
    def fn(a: int, b: int = 2) -> int:
        return a + b

    attrs1 = tracing._resolve_attributes(fn, (3,), {}, {"x": "y"})
    assert attrs1 == {"x": "y"}

    def attr_builder(a: int, b: int = 2):
        return {"sum": a + b}

    attrs2 = tracing._resolve_attributes(fn, (3,), {}, attr_builder)
    assert attrs2 == {"sum": 5}

def test_traced_wraps_sync_and_raises_errors() -> None:
    calls: list[dict] = []

    def attrs(a: int):
        calls.append({"a": a})
        return {"input": a}

    @tracing.traced("unit.sync.span", attributes=attrs)
    def fn(a: int) -> int:
        return a * 2

    assert fn(4) == 8
    assert calls == [{"a": 4}]

    @tracing.traced("unit.sync.err")
    def boom() -> None:
        raise RuntimeError("explode")

    with pytest.raises(RuntimeError, match="explode"):
        boom()

def test_traced_remote_ignores_context_arg_in_attribute_factory() -> None:
    observed: list[dict] = []

    def attrs(self, payload, **kwargs):
        observed.append({"payload": payload, "kwargs": kwargs})
        return {"kind": "ok"}

    class Worker:
        @tracing.traced_remote("unit.remote", context_arg="trace_context", attributes=attrs)
        def run(self, payload: str, trace_context: dict | None = None):
            return f"done:{payload}"

    result = Worker().run("job-1", trace_context={"traceparent": "x"})
    assert result == "done:job-1"
    assert observed[0]["payload"] == "job-1"
    assert observed[0]["kwargs"] == {"trace_context": None}
