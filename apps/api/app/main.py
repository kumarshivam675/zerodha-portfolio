import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .db import init_db
from .routers import agent, portfolio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Portfolio API", lifespan=lifespan)
app.include_router(portfolio.router)
app.include_router(agent.router)


@app.get("/api/health")
def health():
    return {"ok": True, "kite_configured": settings.kite_configured, "agent_configured": settings.agent_configured}
