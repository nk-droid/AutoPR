from fastapi import FastAPI
from contextlib import asynccontextmanager

from apps.api.routes.internal import router as internal_router
from apps.api.routes.runs import router as runs_router
from apps.api.routes.webhooks import get_webhook_queue, router as webhooks_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        if get_webhook_queue.cache_info().currsize > 0:
            await get_webhook_queue().close()

app = FastAPI(title="AutoPR API", lifespan=lifespan)

app.include_router(webhooks_router)
app.include_router(runs_router)
app.include_router(internal_router)
