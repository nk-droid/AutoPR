from prometheus_client import Counter, Histogram, start_http_server

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

def observe_stage(run_type, stage, status, duration_sec: float) -> None:
    labels = [_v(run_type), _v(stage), _v(status)]
    STAGE_RESULTS_TOTAL.labels(*labels).inc()
    STAGE_DURATION_SECONDS.labels(*labels).observe(duration_sec)

def observe_run(run_type, final_state) -> None:
    RUNS_TOTAL.labels(_v(run_type), _v(final_state)).inc()

def start_worker_metrics_server(port: int = 9000) -> None:
    start_http_server(port)