import os
import inspect
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any

from opentelemetry import context, propagate, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanProcessor
from opentelemetry.trace import Status, StatusCode
from opentelemetry.util.types import AttributeValue

from observability.metrics import flush_metrics

SpanAttributes = Mapping[str, AttributeValue]
AttributeFactory = Callable[..., SpanAttributes | None]

_configured = False
def configure_tracing(service_name: str | None = None) -> None:
    global _configured
    if _configured:
        return

    trace_exporter = os.getenv("AUTOPR_TRACE_EXPORTER", "console").lower()
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "localhost:14317")
    service = service_name or os.getenv("AUTOPR_SERVICE_NAME", "autopr")

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": service,
                "deployment.environment": os.getenv("AUTOPR_ENV", "local"),
            }
        )
    )

    processor: SpanProcessor | None = None
    if trace_exporter == "otlp":
        processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True))
    elif trace_exporter == "console":
        processor = BatchSpanProcessor(ConsoleSpanExporter())
    elif trace_exporter != "none":
        raise ValueError(f"Unsupported AUTOPR_TRACE_EXPORTER={trace_exporter!r}")

    if processor is not None:
        provider.add_span_processor(processor)

    trace.set_tracer_provider(provider)
    _configured = True

def get_tracer():
    configure_tracing()
    return trace.get_tracer("autopr")

def inject_trace_context() -> dict[str, str]:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier

def attach_trace_context(carrier: dict[str, str] | None):
    if not carrier:
        return None
    ctx = propagate.extract(carrier)
    return context.attach(ctx)

def detach_trace_context(token) -> None:
    if token is not None:
        context.detach(token)

def _resolve_attributes(
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    attributes: SpanAttributes | AttributeFactory | None,
) -> SpanAttributes:
    if attributes is None:
        return {}

    if not callable(attributes):
        return attributes

    signature = inspect.signature(fn)
    bound = signature.bind_partial(*args, **kwargs)
    bound.apply_defaults()
    return attributes(**bound.arguments) or {}

def traced(name: str, attributes: SpanAttributes | AttributeFactory | None = None):
    def decorator(fn: Callable):
        @wraps(fn)
        async def async_wrapper(*args, **kwargs):
            span_attributes = _resolve_attributes(fn, args, kwargs, attributes)
            with get_tracer().start_as_current_span(name, attributes=span_attributes) as span:
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise

        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            span_attributes = _resolve_attributes(fn, args, kwargs, attributes)
            with get_tracer().start_as_current_span(name, attributes=span_attributes) as span:
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise

        return async_wrapper if inspect.iscoroutinefunction(fn) else sync_wrapper

    return decorator

def _get_bound_arg(
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    name: str,
) -> Any:
    signature = inspect.signature(fn)
    bound = signature.bind_partial(*args, **kwargs)
    return bound.arguments.get(name)

def traced_remote(
    name: str,
    *,
    context_arg: str = "trace_context",
    attributes: SpanAttributes | AttributeFactory | None = None,
):
    def decorator(fn: Callable):
        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            carrier = _get_bound_arg(fn, args, kwargs, context_arg)
            token = attach_trace_context(carrier if isinstance(carrier, dict) else None)

            try:
                attr_kwargs = dict(kwargs)
                attr_kwargs.pop(context_arg, None)

                span_attributes = _resolve_attributes(fn, args, attr_kwargs, attributes)

                with get_tracer().start_as_current_span(name, attributes=span_attributes) as span:
                    try:
                        return fn(*args, **kwargs)
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(Status(StatusCode.ERROR, str(exc)))
                        raise
            finally:
                detach_trace_context(token)
                flush_metrics()

        return sync_wrapper

    return decorator

def ray_worker_attrs(
    self,
    payload: Any = None,
    **_: Any,
) -> dict:
    return {
        "autopr.worker.class": self.__class__.__name__,
        "autopr.payload.type": payload.__class__.__name__ if payload is not None else "",
    }

def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)

def pipeline_step_attrs(self, context: dict[str, Any], run, runtime) -> dict[str, AttributeValue]:
    return {
        "autopr.run_id": str(run.run_id),
        "autopr.run_type": _value(run.run_type),
        "autopr.stage": _value(self.stage),
        "autopr.state": str(run.state),
        "autopr.repository": str(run.repository or context.get("repository") or ""),
    }

def langgraph_node_attrs(agent: str, node: str):
    def factory(state: dict[str, Any] | None = None, **_: Any) -> dict[str, AttributeValue]:
        state = state if isinstance(state, dict) else {}
        status = state.get("status", "")
        return {
            "autopr.agent": agent,
            "autopr.node": node,
            "autopr.status": _value(status) if status else "",
        }

    return factory

def llm_chain_attrs(**kwargs) -> dict[str, AttributeValue]:
    output_model = kwargs.get("output_model")
    return {
        "autopr.llm.output_model": getattr(output_model, "__name__", str(output_model)),
        "autopr.llm.input_vars_count": len(kwargs.get("input_vars") or []),
        "autopr.llm.format_instructions": bool(kwargs.get("include_format_instructions")),
    }
