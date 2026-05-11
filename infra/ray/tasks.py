def run_remote_task(name: str, payload: dict) -> dict:
    return {"task": name, "status": "queued", "payload": payload}
