"""Trade history (for XIRR): Console tradebook CSV import, Kite's same-day trades, and XIRR maths."""

import csv
import io
from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..models import Holding, Trade

# Console header -> our column (headers are lower-cased with spaces/dashes as underscores).
ALIASES = {
    "symbol": "tradingsymbol",
    "tradingsymbol": "tradingsymbol",
    "isin": "isin",
    "trade_date": "trade_date",
    "date": "trade_date",
    "exchange": "exchange",
    "segment": "segment",
    "trade_type": "trade_type",
    "type": "trade_type",
    "transaction_type": "trade_type",
    "quantity": "quantity",
    "qty": "quantity",
    "price": "price",
    "trade_price": "price",
    "trade_id": "trade_id",
}
REQUIRED = {"tradingsymbol", "trade_date", "trade_type", "quantity", "price", "trade_id"}
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y")


def _parse_date(value: str) -> date:
    v = value.strip()
    for candidate in (v, v.split("T")[0], v[:10]):
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    raise ValueError(f"Unrecognised date: {value!r}")


def _upsert(db: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = insert(Trade).values(rows).on_conflict_do_nothing(index_elements=["exchange", "trade_id"])
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0


def import_console_csv(db: Session, text: str) -> dict:
    """Import a Zerodha Console tradebook CSV (equity or MF). Re-importing the same trades is a no-op."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        raise ValueError("The file is empty")
    mapping = {}
    for header in reader.fieldnames:
        key = header.strip().lower().replace(" ", "_").replace("-", "_")
        if key in ALIASES:
            mapping[header] = ALIASES[key]
    missing = REQUIRED - set(mapping.values())
    if missing:
        raise ValueError(
            f"Missing columns: {', '.join(sorted(missing))}. Download the tradebook from Console as CSV."
        )

    rows, skipped = [], 0
    for raw in reader:
        r = {mapping[k]: (v or "").strip() for k, v in raw.items() if k in mapping}
        side = r.get("trade_type", "").lower()
        if side not in ("buy", "sell") or not r.get("trade_id"):
            skipped += 1
            continue
        segment = (r.get("segment") or "").upper()
        if segment and segment not in ("EQ", "MF"):
            skipped += 1  # F&O / currency / commodity don't belong in holdings XIRR
            continue
        try:
            rows.append(
                {
                    "trade_id": r["trade_id"],
                    "tradingsymbol": r["tradingsymbol"].upper(),
                    "isin": r.get("isin") or None,
                    "exchange": (r.get("exchange") or ("MF" if segment == "MF" else "NSE")).upper(),
                    "segment": segment or None,
                    "trade_date": _parse_date(r["trade_date"]),
                    "trade_type": side,
                    "quantity": float(r["quantity"].replace(",", "")),
                    "price": float(r["price"].replace(",", "")),
                    "source": "console",
                }
            )
        except (ValueError, KeyError):
            skipped += 1
    # Deduplicate within the file too (Console exports can repeat rows across ranges).
    unique = list({(row["exchange"], row["trade_id"]): row for row in rows}.values())
    inserted = sum(_upsert(db, unique[i : i + 1000]) for i in range(0, len(unique), 1000))
    dates = [row["trade_date"] for row in unique]
    return {
        "rows": len(unique),
        "inserted": inserted,
        "duplicates": len(unique) - inserted,
        "skipped": skipped,
        "from": min(dates).isoformat() if dates else None,
        "to": max(dates).isoformat() if dates else None,
    }


def import_kite_trades(db: Session, trades: list[dict]) -> int:
    """Store today's executed trades from the Kite API (equity delivery and intraday)."""
    isin_by_symbol = dict(db.execute(select(Holding.tradingsymbol, Holding.isin)).all())
    rows = []
    for t in trades:
        if t.get("exchange") not in ("NSE", "BSE"):
            continue
        ts = t.get("fill_timestamp") or t.get("exchange_timestamp") or datetime.now()
        rows.append(
            {
                "trade_id": str(t["trade_id"]),
                "tradingsymbol": t["tradingsymbol"],
                "isin": isin_by_symbol.get(t["tradingsymbol"]),
                "exchange": t["exchange"],
                "segment": "EQ",
                "trade_date": ts.date() if isinstance(ts, datetime) else _parse_date(str(ts)),
                "trade_type": t["transaction_type"].lower(),
                "quantity": float(t["quantity"]),
                "price": float(t["average_price"]),
                "source": "kite",
            }
        )
    return _upsert(db, rows)


def summary(db: Session) -> dict:
    count, first, last = db.execute(select(func.count(), func.min(Trade.trade_date), func.max(Trade.trade_date))).one()
    return {"count": count, "from": first.isoformat() if first else None, "to": last.isoformat() if last else None}


# --- XIRR ------------------------------------------------------------------


def xirr(flows: list[tuple[date, float]]) -> float | None:
    """Annualised internal rate of return for dated cash flows (negative = money in). As a fraction."""
    flows = [(d, a) for d, a in flows if a]
    if len(flows) < 2 or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    t0 = min(d for d, _ in flows)
    years = [((d - t0).days / 365.0, a) for d, a in flows]
    if max(t for t, _ in years) < 1 / 365:
        return None

    def npv(r: float) -> float:
        return sum(a / (1 + r) ** t for t, a in years)

    # Bracket the root, then bisect: robust where Newton can diverge.
    lo, hi = -0.9999, 1.0
    while npv(hi) > 0 and hi < 1e6:
        hi *= 2
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-9:
            break
    return (lo + hi) / 2


def compute_xirr(db: Session, holdings: list[Holding], today: date) -> dict:
    """XIRR per holding and for the portfolio from stored trades plus today's holding values.

    A holding's history is "full" when net traded quantity equals the held quantity, "partial"
    when trades explain only some units (older trades not imported, bonus/split, transfer-in).
    Partial holdings use the value of the units the trades explain. Instruments sold more
    than bought in the imported history are left out of the portfolio figure.
    """
    trades = list(db.scalars(select(Trade)))
    if not trades:
        return {"portfolio": None, "coverage": 0.0, "holdings": {}, "trades": 0}

    symbol_to_isin = {h.tradingsymbol: h.isin for h in holdings if h.isin}
    by_key: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_key[t.isin or symbol_to_isin.get(t.tradingsymbol) or f"SYM:{t.tradingsymbol}"].append(t)

    held: dict[str, Holding] = {}
    for h in holdings:
        held[h.isin or f"SYM:{h.tradingsymbol}"] = h

    portfolio_flows: list[tuple[date, float]] = []
    per_holding: dict[str, dict] = {}
    covered_value = 0.0
    total_value = sum(h.current_value for h in holdings) or 0.0

    for key in set(by_key) | set(held):
        ts = by_key.get(key, [])
        flows = [(t.trade_date, (-1 if t.trade_type == "buy" else 1) * t.quantity * t.price) for t in ts]
        bought = sum(t.quantity for t in ts if t.trade_type == "buy")
        sold = sum(t.quantity for t in ts if t.trade_type == "sell")
        net = bought - sold
        h = held.get(key)

        if h is None:
            # Fully exited position: realised flows only, if the history explains every sale.
            if ts and sold <= bought + 1e-6:
                portfolio_flows += flows
            continue

        if not ts or net <= 1e-6:
            per_holding[h.tradingsymbol] = {"xirr": None, "coverage": "none"}
            continue
        covered_qty = min(net, h.quantity)
        terminal = h.current_value * covered_qty / h.quantity if h.quantity else 0.0
        status = "full" if abs(net - h.quantity) < 1e-6 else "partial"
        rate = xirr(flows + [(today, terminal)])
        per_holding[h.tradingsymbol] = {"xirr": rate * 100 if rate is not None else None, "coverage": status}
        portfolio_flows += flows + [(today, terminal)]
        covered_value += terminal

    rate = xirr(portfolio_flows)
    return {
        "portfolio": rate * 100 if rate is not None else None,
        "coverage": (covered_value / total_value * 100) if total_value else 0.0,
        "holdings": per_holding,
        "trades": len(trades),
    }
