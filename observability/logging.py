import os
import sys
import uuid
import logging

from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk.resources import Resource

_configured = False
_logger_provider: LoggerProvider | None = None


def setup_logging(service_name: str | None = None) -> None:
    global _configured, _logger_provider

    if _configured:
        return

    log_level_name = os.getenv("AUTOPR_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Quiet noisy third-party INFO chatter (e.g. httpx "HTTP Request: ...") that
    # otherwise drowns out AutoPR's own structured events in the log dashboards.
    for noisy in ("httpx", "httpcore", "urllib3", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Keep normal stdout logs visible in Docker logs.
    if not any(getattr(handler, "_autopr_console", False) for handler in root_logger.handlers):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler._autopr_console = True
        console_handler.setLevel(log_level)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        root_logger.addHandler(console_handler)

    log_exporter = os.getenv("AUTOPR_LOG_EXPORTER", "otlp").lower()
    if log_exporter == "none":
        _configured = True
        return

    if log_exporter != "otlp":
        raise ValueError(f"Unsupported AUTOPR_LOG_EXPORTER={log_exporter!r}")

    service = service_name or os.getenv("AUTOPR_SERVICE_NAME", "autopr")

    resource = Resource.create(
        {
            "service.name": service,
            "service.instance.id": os.getenv("AUTOPR_INSTANCE_ID") or uuid.uuid4().hex,
            "deployment.environment": os.getenv("AUTOPR_ENV", "local"),
        }
    )

    endpoint = (
        os.getenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        or "otel-collector:4317"
    )

    logger_provider = LoggerProvider(resource=resource)
    exporter = OTLPLogExporter(endpoint=endpoint, insecure=True)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    set_logger_provider(logger_provider)

    otel_handler = LoggingHandler(
        level=log_level,
        logger_provider=logger_provider,
    )
    otel_handler._autopr_otel = True

    if not any(getattr(handler, "_autopr_otel", False) for handler in root_logger.handlers):
        root_logger.addHandler(otel_handler)

    _logger_provider = logger_provider
    _configured = True


def flush_logs(timeout_millis: int = 5000) -> None:
    if _logger_provider is not None:
        _logger_provider.force_flush(timeout_millis=timeout_millis)
