def handle_webhook(event_type: str, payload: dict) -> dict:
    return {"event_type": event_type, "accepted": True}
