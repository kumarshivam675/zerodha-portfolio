"""End-of-day prices from Yahoo Finance.

Kite Connect's free Personal tier has no market-data APIs, so daily candles come
from Yahoo Finance (NSE -> SYMBOL.NS, BSE -> SYMBOL.BO). Mutual fund NAVs are only
refreshed on a Zerodha sync.
"""

import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import yfinance as yf
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..models import Holding, PriceHistory
from .portfolio import holding_values

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
SERIES_SUFFIX = re.compile(r"-(BE|BZ|BL|SM|ST|IL|E1|T)$")


def yahoo_ticker(tradingsymbol: str, exchange: str) -> str:
    base = SERIES_SUFFIX.sub("", tradingsymbol.upper())
    return f"{base}.{'BO' if exchange.upper() == 'BSE' else 'NS'}"


def fetch_history(db: Session, tradingsymbol: str, exchange: str, period: str) -> int:
    """Download daily candles for one symbol and upsert them. Returns rows written."""
    df = yf.Ticker(yahoo_ticker(tradingsymbol, exchange)).history(period=period, interval="1d", auto_adjust=False)
    if df is None or df.empty:
        return 0
    rows = [
        {
            "tradingsymbol": tradingsymbol,
            "exchange": exchange,
            "price_date": ts.date(),
            "open": _f(r.get("Open")),
            "high": _f(r.get("High")),
            "low": _f(r.get("Low")),
            "close": float(r["Close"]),
            "volume": int(r["Volume"]) if r.get("Volume") == r.get("Volume") else None,
            "source": "yahoo",
        }
        for ts, r in df.iterrows()
        if r["Close"] == r["Close"]  # skip NaN
    ]
    if not rows:
        return 0
    stmt = insert(PriceHistory).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["exchange", "tradingsymbol", "price_date"],
        set_={c: stmt.excluded[c] for c in ("open", "high", "low", "close", "volume", "source")},
    )
    db.execute(stmt)
    db.commit()
    return len(rows)


def _f(v) -> float | None:
    return float(v) if v is not None and v == v else None


def update_equity_prices(db: Session) -> dict:
    """Fetch recent candles for every equity holding and reprice the holdings from them."""
    holdings = list(db.scalars(select(Holding).where(Holding.kind == "equity")))
    fetched, failed = 0, []
    for h in holdings:
        has_history = db.scalar(
            select(func.count())
            .select_from(PriceHistory)
            .where(PriceHistory.tradingsymbol == h.tradingsymbol, PriceHistory.exchange == h.exchange)
        )
        try:
            fetched += fetch_history(db, h.tradingsymbol, h.exchange, "1mo" if has_history else "1y")
        except Exception as exc:  # one bad symbol shouldn't stop the job
            log.warning("price fetch failed for %s: %s", h.tradingsymbol, exc)
            db.rollback()
            failed.append(h.tradingsymbol)

    now = datetime.now(IST)
    repriced = 0
    for h in holdings:
        latest = list(
            db.scalars(
                select(PriceHistory)
                .where(PriceHistory.tradingsymbol == h.tradingsymbol, PriceHistory.exchange == h.exchange)
                .order_by(PriceHistory.price_date.desc())
                .limit(2)
            )
        )
        if not latest:
            continue
        close = latest[1].close if len(latest) > 1 else h.close_price
        for k, v in holding_values(h.quantity, h.average_price, latest[0].close, close).items():
            setattr(h, k, v)
        h.price_updated_at = now
        repriced += 1
    db.commit()
    return {"candles_written": fetched, "repriced": repriced, "failed": failed}


def price_history(db: Session, tradingsymbol: str, exchange: str = "NSE", days: int = 365) -> list[dict]:
    rows = list(
        db.scalars(
            select(PriceHistory)
            .where(PriceHistory.tradingsymbol == tradingsymbol.upper(), PriceHistory.exchange == exchange.upper())
            .order_by(PriceHistory.price_date.desc())
            .limit(days)
        )
    )
    return [
        {
            "date": r.price_date.isoformat(),
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
        }
        for r in reversed(rows)
    ]
