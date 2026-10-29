from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
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


BASELINE_REVISION = "0001"
MIGRATION_LOCK = 724_201  # arbitrary, app-wide advisory lock id


def migrate() -> None:
    """Bring the schema to the latest Alembic revision (app/migrations).

    Databases created before migrations existed (by `create_all`) are stamped at the baseline first.
    """
    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with engine.begin() as conn:
        # The API and the worker both migrate on startup; serialise them.
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
        config.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        if "alembic_version" not in tables and "holdings" in tables:
            command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")


def init_db() -> None:
    migrate()
    with engine.begin() as conn:
        conn.execute(text("GRANT USAGE ON SCHEMA public TO agent_ro"))
        conn.execute(text(f"GRANT SELECT ON {', '.join(AGENT_READABLE_TABLES)} TO agent_ro"))
