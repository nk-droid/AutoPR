import logging
from typing import Any

from core.orchestrator.models import StageStatus

_SUCCESS_STATUS = {StageStatus.OK, StageStatus.ACCEPTED}
_RESUMABLE_STATUS = {StageStatus.NEEDS_REVIEW}
_FAILED_STATUS = {StageStatus.BLOCKED, StageStatus.FAILED}


def log_agent_decision(logger: logging.Logger, agent: str, status: Any, **extra: Any) -> None:
    status_label = status.value if hasattr(status, "value") else str(status)

    if status_label in _SUCCESS_STATUS:
        level = logging.INFO
    elif status_label in _RESUMABLE_STATUS:
        level = logging.WARNING
    elif status_label in _FAILED_STATUS:
        level = logging.ERROR
    else:
        level = logging.WARNING

    logger.log(
        level,
        f"agent[{agent}] decision -> {status_label}",
        extra={
            "event": "agent_decision",
            "agent": agent,
            "status": status_label,
            **extra,
        },
    )
