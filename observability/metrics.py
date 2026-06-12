import os
import uuid

from prometheus_client import Counter, Histogram, Gauge, start_http_server

from opentelemetry import metrics as otel_metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter


def _v(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


WEBHOOKS_TOTAL = Counter(
    "autopr_webhooks_total",
    "GitHub webhooks handled by result.",
    ["event_type", "result"],
)

WEBHOOK_JOBS_ENQUEUED_TOTAL = Counter(
    "autopr_webhook_jobs_enqueued_total",
    "Webhook jobs enqueued.",
    ["run_type"],
)

QUEUE_MESSAGES_TOTAL = Counter(
    "autopr_queue_messages_total",
    "Queue messages processed.",
    ["action", "run_type", "result"],
)

QUEUE_DEPTH = Gauge(
    "autopr_queue_depth",
    "Messages currently in the webhook queue by state.",
    ["queue"],  # pending | processing | dlq
)

RUNS_TOTAL = Counter(
    "autopr_runs_total",
    "Pipeline runs completed.",
    ["run_type", "final_state"],
)

STAGE_RESULTS_TOTAL = Counter(
    "autopr_stage_results_total",
    "Pipeline stage results.",
    ["run_type", "stage", "status"],
)

STAGE_DURATION_SECONDS = Histogram(
    "autopr_stage_duration_seconds",
    "Pipeline stage duration.",
    ["run_type", "stage", "status"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1200),
)

QA_TOOL_RESULTS_TOTAL = Counter(
    "autopr_qa_tool_results_total",
    "QA tool results.",
    ["tool", "status"],
)

QA_TOOL_DURATION_SECONDS = Histogram(
    "autopr_qa_tool_duration_seconds",
    "QA tool duration.",
    ["tool", "status"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)

_metrics_configured = False


def configure_metrics(service_name: str | None = None) -> None:
    """
    Configure OpenTelemetry metrics once for the current process.

    Args:
        service_name: Optional service name overriding AUTOPR_SERVICE_NAME.
    """

    global _metrics_configured
    if _metrics_configured:
        return

    exporter_kind = os.getenv("AUTOPR_METRICS_EXPORTER", "none").lower()
    if exporter_kind == "none":
        _metrics_configured = True
        return

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "localhost:14317")
    service = service_name or os.getenv("AUTOPR_SERVICE_NAME", "autopr")

    if exporter_kind == "otlp":
        exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True)
    elif exporter_kind == "console":
        exporter = ConsoleMetricExporter()
    else:
        raise ValueError(f"Unsupported AUTOPR_METRICS_EXPORTER={exporter_kind!r}")

    provider = MeterProvider(
        resource=Resource.create(
            {
                "service.name": service,
                "service.instance.id": os.getenv("AUTOPR_INSTANCE_ID") or uuid.uuid4().hex,
                "deployment.environment": os.getenv("AUTOPR_ENV", "local"),
            }
        ),
        metric_readers=[PeriodicExportingMetricReader(exporter)],
        views=[
            View(
                instrument_name="autopr_llm_request_duration_seconds",
                aggregation=ExplicitBucketHistogramAggregation(
                    (0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300)
                ),
            ),
            View(
                instrument_name="autopr_llm_tokens_per_request",
                aggregation=ExplicitBucketHistogramAggregation(
                    (50, 100, 250, 500, 1000, 2000, 4000, 8000, 16000, 32000, 64000)
                ),
            ),
            View(
                instrument_name="autopr_llm_tokens_per_second",
                aggregation=ExplicitBucketHistogramAggregation((1, 5, 10, 25, 50, 100, 250, 500)),
            ),
        ],
    )
    otel_metrics.set_meter_provider(provider)
    _metrics_configured = True


def flush_metrics(timeout_millis: int = 5000) -> None:
    """
    Force metric export when the configured provider supports flushing.

    Args:
        timeout_millis: Maximum time to wait for metric export.
    """

    if not _metrics_configured:
        return
    force_flush = getattr(otel_metrics.get_meter_provider(), "force_flush", None)
    if force_flush is not None:
        force_flush(timeout_millis=timeout_millis)


_meter = otel_metrics.get_meter("autopr")

LLM_REQUESTS_TOTAL = _meter.create_counter(
    "autopr_llm_requests",
    description="LLM calls by outcome.",
)

LLM_REQUEST_DURATION_SECONDS = _meter.create_histogram(
    "autopr_llm_request_duration_seconds",
    description="LLM call wall-clock duration.",
)

LLM_IN_FLIGHT = _meter.create_up_down_counter(
    "autopr_llm_in_flight",
    description="LLM calls currently in flight.",
)

LLM_INPUT_TOKENS_TOTAL = _meter.create_counter(
    "autopr_llm_input_tokens",
    description="Prompt tokens consumed.",
)

LLM_OUTPUT_TOKENS_TOTAL = _meter.create_counter(
    "autopr_llm_output_tokens",
    description="Completion tokens generated.",
)

LLM_TOKENS_PER_REQUEST = _meter.create_histogram(
    "autopr_llm_tokens_per_request",
    description="Token counts per single LLM call.",
)

LLM_TOKENS_PER_SECOND = _meter.create_histogram(
    "autopr_llm_tokens_per_second",
    description="Output tokens per second of wall-clock.",
)

LLM_COST_USD_TOTAL = _meter.create_counter(
    "autopr_llm_cost_usd",
    description="Estimated LLM spend in USD.",
)

LLM_PARSE_ERRORS_TOTAL = _meter.create_counter(
    "autopr_llm_parse_errors",
    description="Pydantic validation failures on LLM output.",
)


def observe_stage(run_type, stage, status, duration_sec: float) -> None:
    """
    Record pipeline stage result and duration metrics.

    Args:
        run_type: Workflow type for the completed stage.
        stage: Pipeline stage that completed.
        status: Stage status returned by execution.
        duration_sec: Wall-clock stage duration in seconds.
    """

    labels = [_v(run_type), _v(stage), _v(status)]
    STAGE_RESULTS_TOTAL.labels(*labels).inc()
    STAGE_DURATION_SECONDS.labels(*labels).observe(duration_sec)


def observe_run(run_type, final_state) -> None:
    """
    Record a completed pipeline run by workflow type and final state.

    Args:
        run_type: Workflow type for the completed run.
        final_state: Final state reached by the run.
    """

    RUNS_TOTAL.labels(_v(run_type), _v(final_state)).inc()


def start_worker_metrics_server(port: int = 9000) -> None:
    """
    Start the Prometheus HTTP server used by worker processes.

    Args:
        port: TCP port for exposing Prometheus metrics.
    """

    start_http_server(port)
