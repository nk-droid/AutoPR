from fastapi import FastAPI

from apps.api.routes.internal import router as internal_router
from apps.api.routes.runs import router as runs_router
from apps.api.routes.webhooks import router as webhooks_router

app = FastAPI(title="AutoPR API")

app.include_router(webhooks_router)
app.include_router(runs_router)
app.include_router(internal_router)
