from fastapi import APIRouter, HTTPException

from infra.storage.artifacts import load_run

router = APIRouter(prefix="/runs", tags=["runs"])

@router.get("/{run_id}")
def get_run(run_id: str) -> dict:
    run = load_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return run.model_dump()
