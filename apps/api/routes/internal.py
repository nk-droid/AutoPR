from fastapi import APIRouter

router = APIRouter(prefix="/internal", tags=["internal"])

@router.post("/agent-result")
def agent_result() -> dict:
    # Internal callback placeholder for asynchronous agent signaling.
    return {"status": "ok"}
