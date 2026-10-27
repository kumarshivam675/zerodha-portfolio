import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class KiteSession(Base):
    """The single active Kite Connect session. Access tokens expire daily (~6 AM IST)."""

    __tablename__ = "kite_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    user_id: Mapped[str] = mapped_column(String(32))
    user_name: Mapped[str | None] = mapped_column(String(128))
    access_token: Mapped[str] = mapped_column(String(128))
    login_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    invalidated: Mapped[bool] = mapped_column(default=False)


class HoldingColumns:
    kind: Mapped[str] = mapped_column(String(8))  # "equity" | "mf"
    tradingsymbol: Mapped[str] = mapped_column(String(64))
    exchange: Mapped[str] = mapped_column(String(8))
    isin: Mapped[str | None] = mapped_column(String(16))
    name: Mapped[str | None] = mapped_column(String(256))
    quantity: Mapped[float] = mapped_column(Float)
    average_price: Mapped[float] = mapped_column(Float)
    last_price: Mapped[float] = mapped_column(Float)
    close_price: Mapped[float | None] = mapped_column(Float)
    invested: Mapped[float] = mapped_column(Float)
    current_value: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float)
    day_change: Mapped[float | None] = mapped_column(Float)
    day_change_percentage: Mapped[float | None] = mapped_column(Float)


class Holding(HoldingColumns, Base):
    """Current portfolio, replaced on every Zerodha sync; prices refreshed by the daily job."""

    __tablename__ = "holdings"
    __table_args__ = (UniqueConstraint("kind", "exchange", "tradingsymbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    price_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Snapshot(Base):
    """A frozen copy of the portfolio for one day. Re-capturing the same day replaces it."""

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, unique=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(String(16))  # "manual" | "scheduled"
    invested: Mapped[float] = mapped_column(Float)
    current_value: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float)
    day_change: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)

    holdings: Mapped[list["SnapshotHolding"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", order_by="SnapshotHolding.current_value.desc()"
    )


class SnapshotHolding(HoldingColumns, Base):
    __tablename__ = "snapshot_holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"), index=True)
    snapshot: Mapped[Snapshot] = relationship(back_populates="holdings")


class PriceHistory(Base):
    """Daily OHLCV candles (end-of-day)."""

    __tablename__ = "price_history"
    __table_args__ = (UniqueConstraint("exchange", "tradingsymbol", "price_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tradingsymbol: Mapped[str] = mapped_column(String(64), index=True)
    exchange: Mapped[str] = mapped_column(String(8))
    price_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(16))


class Trade(Base):
    """Executed equity/MF trades: imported from Console tradebook CSVs and today's trades from Kite."""

    __tablename__ = "trades"
    __table_args__ = (UniqueConstraint("exchange", "trade_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(64))
    tradingsymbol: Mapped[str] = mapped_column(String(64), index=True)
    isin: Mapped[str | None] = mapped_column(String(16), index=True)
    exchange: Mapped[str] = mapped_column(String(8))
    segment: Mapped[str | None] = mapped_column(String(8))
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    trade_type: Mapped[str] = mapped_column(String(4))  # "buy" | "sell"
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16))  # "console" | "kite"
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DailyBrief(Base):
    """Agent-written portfolio review produced by the scheduled job."""

    __tablename__ = "daily_briefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_date: Mapped[date] = mapped_column(Date, index=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job: Mapped[str] = mapped_column(String(32))
    trigger: Mapped[str] = mapped_column(String(16))  # "schedule" | "manual"
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")
    detail: Mapped[dict | None] = mapped_column(JSON)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="ConversationMessage.seq"
    )


class ConversationMessage(Base):
    """One Messages-API turn, stored verbatim so history can be replayed append-only."""

    __tablename__ = "conversation_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
