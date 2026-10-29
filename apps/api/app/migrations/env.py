"""Alembic environment: migrates the database the app is configured for (DATABASE_URL)."""

from alembic import context

from app import models  # noqa: F401  (register models on Base.metadata)
from app.db import Base, engine

target_metadata = Base.metadata


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:  # called from app.db.migrate() with an open connection
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
