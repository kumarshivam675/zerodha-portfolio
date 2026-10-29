import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import get_settings
from .db import init_db
from .routers import agent, portfolio
from .security import SameOriginWrites
from .services import runs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    runs.recover_interrupted()
    yield


app = FastAPI(title="Portfolio API", lifespan=lifespan)
app.add_middleware(SameOriginWrites, allowed_origins={settings.web_url})
# DNS rebinding: only answer to the names the API is really reached by (see security.py).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.api_allowed_hosts)
app.include_router(portfolio.router)
app.include_router(agent.router)


@app.get("/api/health")
def health():
    return {"ok": True, "kite_configured": settings.kite_configured, "agent_configured": settings.agent_configured}
