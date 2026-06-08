import os
import ray

def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}

def ensure_ray_initialized() -> None:
    if ray.is_initialized():
        return

    address = os.getenv("RAY_ADDRESS")
    if address:
        ray.init(address=address, ignore_reinit_error=True)
        return
    include_dashboard = _env_flag("RAY_INCLUDE_DASHBOARD", True)
    dashboard_port = int(os.getenv("RAY_DASHBOARD_PORT", "8265"))
    ray.init(
        include_dashboard=include_dashboard,
        dashboard_host="0.0.0.0",
        dashboard_port=dashboard_port,
        ignore_reinit_error=True,
    )
