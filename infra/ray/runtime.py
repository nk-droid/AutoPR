import os
import ray


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _setup_worker_observability() -> None:
    """Configure log export inside each Ray worker process.

    Ray workers are separate processes that never run the api/worker entrypoints,
    so logs emitted by agents, QA jobs, and the LLM gateway only reach Loki when
    each worker installs the OTLP logging handler on startup.
    """

    from observability.logging import setup_logging

    setup_logging()


def _runtime_env() -> dict:
    return {"worker_process_setup_hook": _setup_worker_observability}


def ensure_ray_initialized() -> None:
    if ray.is_initialized():
        return

    address = os.getenv("RAY_ADDRESS")
    if address:
        ray.init(address=address, runtime_env=_runtime_env(), ignore_reinit_error=True)
        return
    include_dashboard = _env_flag("RAY_INCLUDE_DASHBOARD", True)
    dashboard_host = os.getenv("RAY_DASHBOARD_HOST", "127.0.0.1")
    dashboard_port = int(os.getenv("RAY_DASHBOARD_PORT", "8265"))
    ray.init(
        include_dashboard=include_dashboard,
        dashboard_host=dashboard_host,
        dashboard_port=dashboard_port,
        runtime_env=_runtime_env(),
        ignore_reinit_error=True,
    )
