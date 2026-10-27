"""Portfolio maths, serialization and snapshots."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Holding, Snapshot, SnapshotHolding
from . import trades as trade_history

IST = ZoneInfo("Asia/Kolkata")

HOLDING_FIELDS = [
    "kind",
    "tradingsymbol",
    "exchange",
    "isin",
    "name",
    "quantity",
    "average_price",
    "last_price",
    "close_price",
    "invested",
    "current_value",
    "pnl",
    "day_change",
    "day_change_percentage",
]


def holding_values(qty: float, avg: float, last: float, close: float | None) -> dict:
    """Derived values for one holding. `day_change` is the total rupee change vs previous close."""
    invested = qty * avg
    current = qty * last
    day_change = qty * (last - close) if close else None
    return {
        "quantity": qty,
        "average_price": avg,
        "last_price": last,
        "close_price": close,
        "invested": invested,
        "current_value": current,
        "pnl": current - invested,
        "day_change": day_change,
        "day_change_percentage": ((last - close) / close * 100) if close else None,
    }


def serialize_holding(h: Holding | SnapshotHolding) -> dict:
    d = {f: getattr(h, f) for f in HOLDING_FIELDS}
    d["pnl_percentage"] = (h.pnl / h.invested * 100) if h.invested else None
    return d


def totals(holdings: list) -> dict:
    invested = sum(h.invested for h in holdings)
    current = sum(h.current_value for h in holdings)
    day_changes = [h.day_change for h in holdings if h.day_change is not None]
    day_change = sum(day_changes) if day_changes else None
    prev_value = current - day_change if day_change is not None else None
    return {
        "invested": invested,
        "current_value": current,
        "pnl": current - invested,
        "pnl_percentage": ((current - invested) / invested * 100) if invested else None,
        "day_change": day_change,
        "day_change_percentage": (day_change / prev_value * 100) if prev_value else None,
        "count": len(holdings),
    }


def current_portfolio(db: Session) -> dict:
    holdings = list(db.scalars(select(Holding).order_by(Holding.current_value.desc())))
    current_value = sum(h.current_value for h in holdings) or 1
    xirr = trade_history.compute_xirr(db, holdings, datetime.now(IST).date())
    items = []
    for h in holdings:
        d = serialize_holding(h)
        d["weight"] = h.current_value / current_value * 100
        hx = xirr["holdings"].get(h.tradingsymbol, {"xirr": None, "coverage": "none"})
        d["xirr"], d["xirr_coverage"] = hx["xirr"], hx["coverage"]
        items.append(d)
    return {
        "totals": {
            **totals(holdings),
            "xirr": xirr["portfolio"],
            "xirr_coverage": xirr["coverage"],
            "trades_imported": xirr["trades"],
        },
        "holdings": items,
        "synced_at": max((h.synced_at for h in holdings), default=None),
        "price_updated_at": max((h.price_updated_at for h in holdings), default=None),
    }


def take_snapshot(db: Session, source: str, note: str | None = None, on: date | None = None) -> Snapshot:
    """Freeze current holdings as the snapshot for `on` (default: today, IST)."""
    holdings = list(db.scalars(select(Holding)))
    if not holdings:
        raise ValueError("No holdings to snapshot; sync from Zerodha first")
    snap_date = on or datetime.now(IST).date()
    db.execute(delete(Snapshot).where(Snapshot.snapshot_date == snap_date))
    t = totals(holdings)
    snap = Snapshot(
        snapshot_date=snap_date,
        source=source,
        invested=t["invested"],
        current_value=t["current_value"],
        pnl=t["pnl"],
        day_change=t["day_change"],
        note=note,
        holdings=[SnapshotHolding(**{f: getattr(h, f) for f in HOLDING_FIELDS}) for h in holdings],
    )
    db.add(snap)
    db.commit()
    return snap


def serialize_snapshot(s: Snapshot, with_holdings: bool = False) -> dict:
    d = {
        "id": s.id,
        "snapshot_date": s.snapshot_date.isoformat(),
        "taken_at": s.taken_at.isoformat() if s.taken_at else None,
        "source": s.source,
        "invested": s.invested,
        "current_value": s.current_value,
        "pnl": s.pnl,
        "pnl_percentage": (s.pnl / s.invested * 100) if s.invested else None,
        "day_change": s.day_change,
        "note": s.note,
    }
    if with_holdings:
        d["holdings"] = [serialize_holding(h) for h in s.holdings]
    return d
