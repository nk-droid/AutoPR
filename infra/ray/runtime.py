import ray

def ensure_ray_initialized() -> None:
    if ray.is_initialized():
        return
    ray.init(
        include_dashboard=True,
        dashboard_host="0.0.0.0",
        dashboard_port=8265,
        ignore_reinit_error=True,
    )
