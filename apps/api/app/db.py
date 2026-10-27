from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# Read-only engine for the agent's ad-hoc SQL tool. The role it logs in as has
# SELECT on portfolio tables only (no access to stored Kite tokens).
agent_engine = create_engine(settings.agent_database_url, pool_pre_ping=True, pool_size=2)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# Tables the agent's read-only role may SELECT from.
AGENT_READABLE_TABLES = [
    "holdings",
    "snapshots",
    "snapshot_holdings",
    "price_history",
    "trades",
    "daily_briefs",
    "job_runs",
]


def init_db() -> None:
    from . import models  # noqa: F401  (register models)

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("GRANT USAGE ON SCHEMA public TO agent_ro"))
        conn.execute(text(f"GRANT SELECT ON {', '.join(AGENT_READABLE_TABLES)} TO agent_ro"))
