"""Agent runs and Analyse pipeline steps.

if_not_exists: early versions of the multi-agent branch created these tables with create_all.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_runs_conversation_id"), "agent_runs", ["conversation_id"], unique=False, if_not_exists=True
    )
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("agent", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_steps_conversation_id"), "agent_steps", ["conversation_id"], unique=False, if_not_exists=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_steps_conversation_id"), table_name="agent_steps", if_exists=True)
    op.drop_table("agent_steps", if_exists=True)
    op.drop_index(op.f("ix_agent_runs_conversation_id"), table_name="agent_runs", if_exists=True)
    op.drop_table("agent_runs", if_exists=True)
