"""Zerodha Kite Connect: login flow, session storage, and portfolio sync."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Holding, KiteSession
from . import trades as trade_history
from .portfolio import holding_values

settings = get_settings()
IST = ZoneInfo("Asia/Kolkata")


class KiteNotConnected(Exception):
    pass


def _client(access_token: str | None = None) -> KiteConnect:
    if not settings.kite_configured:
        raise KiteNotConnected("KITE_API_KEY / KITE_API_SECRET are not set")
    kite = KiteConnect(api_key=settings.kite_api_key)
    if access_token:
        kite.set_access_token(access_token)
    return kite


def login_url() -> str:
    return _client().login_url()


def complete_login(db: Session, request_token: str) -> KiteSession:
    kite = _client()
    data = kite.generate_session(request_token, api_secret=settings.kite_api_secret)
    login_time = data.get("login_time") or datetime.now(IST)
    if login_time.tzinfo is None:
        login_time = login_time.replace(tzinfo=IST)
    session = db.get(KiteSession, 1) or KiteSession(id=1)
    session.user_id = data["user_id"]
    session.user_name = data.get("user_name")
    session.access_token = data["access_token"]
    session.login_time = login_time
    session.invalidated = False
    db.add(session)
    db.commit()
    return session


def _last_expiry(now: datetime) -> datetime:
    """Kite tokens are flushed at ~06:00 IST every day."""
    six_am = datetime.combine(now.date(), time(6, 0), tzinfo=IST)
    return six_am if now >= six_am else six_am - timedelta(days=1)


def active_session(db: Session) -> KiteSession | None:
    session = db.get(KiteSession, 1)
    if not session or session.invalidated:
        return None
    if session.login_time < _last_expiry(datetime.now(IST)):
        return None
    return session


def status(db: Session) -> dict:
    session = db.get(KiteSession, 1)
    active = active_session(db)
    return {
        "configured": settings.kite_configured,
        "connected": active is not None,
        "user_id": session.user_id if session else None,
        "user_name": session.user_name if session else None,
        "login_time": session.login_time.isoformat() if session else None,
    }


def sync_holdings(db: Session) -> dict:
    """Replace the current holdings with what Kite reports (equity + mutual funds)."""
    session = active_session(db)
    if not session:
        raise KiteNotConnected("Zerodha session missing or expired; log in again")
    kite = _client(session.access_token)
    try:
        equity = kite.holdings()
        try:
            mfs = kite.mf_holdings()
        except Exception:  # MF access can be unavailable on some app types
            mfs = []
    except TokenException as exc:
        session.invalidated = True
        db.commit()
        raise KiteNotConnected("Zerodha session expired; log in again") from exc

    now = datetime.now(IST)
    rows: list[Holding] = []
    for h in equity:
        qty = float(h.get("quantity", 0)) + float(h.get("t1_quantity", 0))
        if qty <= 0:
            continue
        rows.append(
            Holding(
                kind="equity",
                tradingsymbol=h["tradingsymbol"],
                exchange=h["exchange"],
                isin=h.get("isin"),
                name=None,
                synced_at=now,
                price_updated_at=now,
                **holding_values(qty, h["average_price"], h["last_price"], h.get("close_price")),
            )
        )
    for m in mfs:
        qty = float(m.get("quantity", 0))
        if qty <= 0:
            continue
        rows.append(
            Holding(
                kind="mf",
                tradingsymbol=m["tradingsymbol"],  # ISIN for mutual funds
                exchange="MF",
                isin=m["tradingsymbol"],
                name=m.get("fund"),
                synced_at=now,
                price_updated_at=now,
                **holding_values(qty, m["average_price"], m["last_price"], None),
            )
        )

    db.execute(delete(Holding))
    db.add_all(rows)
    db.commit()

    # Kite only exposes today's trades; keeping them extends the imported tradebook for XIRR.
    try:
        new_trades = trade_history.import_kite_trades(db, kite.trades())
    except Exception:
        db.rollback()
        new_trades = 0
    return {
        "equity": sum(r.kind == "equity" for r in rows),
        "mf": sum(r.kind == "mf" for r in rows),
        "trades": new_trades,
    }


def has_holdings(db: Session) -> bool:
    return db.scalar(select(Holding.id).limit(1)) is not None
