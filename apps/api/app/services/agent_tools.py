"""Client-side tools the portfolio agent can call. Each tool validates its input with pydantic."""

import json
from datetime import date

import yfinance as yf
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, text

from ..db import AGENT_READABLE_TABLES, agent_engine, session_scope
from ..models import Snapshot
from . import portfolio, prices


class NoInput(BaseModel):
    pass


class ListSnapshotsInput(BaseModel):
    limit: int = Field(30, ge=1, le=365)


class GetSnapshotInput(BaseModel):
    snapshot_date: date


class PriceHistoryInput(BaseModel):
    tradingsymbol: str
    exchange: str = "NSE"
    days: int = Field(90, ge=1, le=1500)


class TechnicalsInput(BaseModel):
    tradingsymbol: str
    exchange: str = "NSE"


class StockInfoInput(BaseModel):
    tradingsymbol: str
    exchange: str = "NSE"


class SqlInput(BaseModel):
    sql: str


SCHEMA_DOC = """\
holdings(kind 'equity'|'mf', tradingsymbol, exchange, isin, name, quantity, average_price, last_price,
         close_price, invested, current_value, pnl, day_change [rupees vs prev close], day_change_percentage,
         synced_at, price_updated_at)  -- current portfolio
snapshots(id, snapshot_date, taken_at, source, invested, current_value, pnl, day_change, note)
snapshot_holdings(snapshot_id -> snapshots.id, same columns as holdings minus timestamps)
price_history(tradingsymbol, exchange, price_date, open, high, low, close, volume, source)  -- daily candles
trades(trade_id, tradingsymbol, isin, exchange, segment, trade_date, trade_type 'buy'|'sell', quantity, price,
       source 'console'|'kite')  -- executed trades imported from the Console tradebook (may not cover all history)
daily_briefs(brief_date, content, created_at)
job_runs(job, trigger, started_at, finished_at, status, detail)"""


def _tool(name: str, description: str, model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return {
        "name": name,
        "description": description,
        "input_schema": schema,
        "eager_input_streaming": True,
    }


CLIENT_TOOLS = [
    _tool(
        "get_portfolio",
        "Current holdings (equity and mutual funds) with quantity, average cost, last price, invested, "
        "current value, P&L, day change, portfolio weight and XIRR (with how much of the holding the imported "
        "trade history covers), plus totals including portfolio XIRR, and when data was last synced.",
        NoInput,
    ),
    _tool("list_snapshots", "Saved daily portfolio snapshots (totals only), newest first.", ListSnapshotsInput),
    _tool("get_snapshot", "Full holdings of the snapshot saved on a given date (YYYY-MM-DD).", GetSnapshotInput),
    _tool(
        "get_price_history",
        "Daily OHLCV candles for an Indian stock (Kite tradingsymbol, e.g. INFY on NSE). Uses stored data, "
        "downloading it first if the symbol has none.",
        PriceHistoryInput,
    ),
    _tool(
        "get_technicals",
        "Computed performance and technical indicators for an Indian stock from up to 1 year of daily closes: "
        "returns over 1W/1M/3M/6M/1Y, 20/50/200-day moving averages and price vs each, RSI(14), annualised "
        "volatility, max drawdown, 52-week high/low and distance from them, and average volume trend. Prefer "
        "this over computing indicators yourself from raw candles.",
        TechnicalsInput,
    ),
    _tool(
        "get_stock_info",
        "Fundamentals and valuation snapshot for an Indian stock from Yahoo Finance: sector, market cap, P/E, "
        "P/B, dividend yield, 52-week range, analyst targets, business summary.",
        StockInfoInput,
    ),
    _tool(
        "query_database",
        "Run one read-only SQL SELECT against the portfolio database (PostgreSQL) for analysis the other tools "
        f"don't cover. Results are capped at 200 rows. Tables:\n{SCHEMA_DOC}",
        SqlInput,
    ),
]

SERVER_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 15},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 8},
]

TOOLS = CLIENT_TOOLS + SERVER_TOOLS


def _get_portfolio(_: NoInput):
    with session_scope() as db:
        return portfolio.current_portfolio(db)


def _list_snapshots(inp: ListSnapshotsInput):
    with session_scope() as db:
        snaps = db.scalars(select(Snapshot).order_by(Snapshot.snapshot_date.desc()).limit(inp.limit))
        return [portfolio.serialize_snapshot(s) for s in snaps]


def _get_snapshot(inp: GetSnapshotInput):
    with session_scope() as db:
        snap = db.scalar(select(Snapshot).where(Snapshot.snapshot_date == inp.snapshot_date))
        if not snap:
            raise ValueError(f"No snapshot saved on {inp.snapshot_date}")
        return portfolio.serialize_snapshot(snap, with_holdings=True)


def _get_price_history(inp: PriceHistoryInput):
    symbol, exchange = inp.tradingsymbol.upper(), inp.exchange.upper()
    with session_scope() as db:
        rows = prices.price_history(db, symbol, exchange, inp.days)
        if not rows:
            prices.fetch_history(db, symbol, exchange, "5y" if inp.days > 365 else "1y")
            rows = prices.price_history(db, symbol, exchange, inp.days)
    if not rows:
        raise ValueError(f"No price data found for {symbol} on {exchange}")
    return rows


def _load_closes(symbol: str, exchange: str) -> list[dict]:
    with session_scope() as db:
        rows = prices.price_history(db, symbol, exchange, 400)
        if len(rows) < 200:
            prices.fetch_history(db, symbol, exchange, "2y")
            rows = prices.price_history(db, symbol, exchange, 400)
    return rows


def _get_technicals(inp: TechnicalsInput):
    symbol, exchange = inp.tradingsymbol.upper(), inp.exchange.upper()
    rows = _load_closes(symbol, exchange)
    if len(rows) < 20:
        raise ValueError(f"Not enough price history for {symbol} on {exchange}")
    closes = [r["close"] for r in rows]
    last = closes[-1]

    def ret(days: int):
        return round((last / closes[-days - 1] - 1) * 100, 2) if len(closes) > days else None

    def sma(n: int):
        return round(sum(closes[-n:]) / n, 2) if len(closes) >= n else None

    gains, losses = [], []
    for a, b in zip(closes[-15:-1], closes[-14:]):
        gains.append(max(b - a, 0))
        losses.append(max(a - b, 0))
    avg_loss = sum(losses) / 14
    rsi = round(100 - 100 / (1 + (sum(gains) / 14) / avg_loss), 1) if avg_loss else 100.0

    year = closes[-252:]
    daily = [b / a - 1 for a, b in zip(year[:-1], year[1:])]
    mean = sum(daily) / len(daily)
    vol = (sum((d - mean) ** 2 for d in daily) / len(daily)) ** 0.5 * (252**0.5) * 100
    peak, max_dd = year[0], 0.0
    for c in year:
        peak = max(peak, c)
        max_dd = min(max_dd, c / peak - 1)
    hi, lo = max(year), min(year)
    vols = [r["volume"] or 0 for r in rows]
    smas = {n: sma(n) for n in (20, 50, 200)}
    return {
        "as_of": rows[-1]["date"],
        "last_close": round(last, 2),
        "returns_pct": {"1W": ret(5), "1M": ret(21), "3M": ret(63), "6M": ret(126), "1Y": ret(252)},
        "moving_averages": smas,
        "price_vs_ma_pct": {f"SMA{n}": round((last / v - 1) * 100, 2) for n, v in smas.items() if v},
        "rsi_14": rsi,
        "annualised_volatility_pct": round(vol, 1),
        "max_drawdown_1y_pct": round(max_dd * 100, 1),
        "high_52w": round(hi, 2),
        "low_52w": round(lo, 2),
        "from_52w_high_pct": round((last / hi - 1) * 100, 2),
        "from_52w_low_pct": round((last / lo - 1) * 100, 2),
        "avg_volume_20d": round(sum(vols[-20:]) / 20) if len(vols) >= 20 else None,
        "avg_volume_6m": round(sum(vols[-126:]) / len(vols[-126:])) if vols else None,
        "trading_days_of_data": len(closes),
    }


INFO_KEYS = [
    "longName", "sector", "industry", "marketCap", "currentPrice", "previousClose", "trailingPE", "forwardPE",
    "priceToBook", "trailingEps", "dividendYield", "returnOnEquity", "debtToEquity", "profitMargins",
    "revenueGrowth", "earningsGrowth", "fiftyTwoWeekLow", "fiftyTwoWeekHigh", "fiftyDayAverage",
    "twoHundredDayAverage", "beta", "targetMeanPrice", "recommendationKey", "numberOfAnalystOpinions",
    "longBusinessSummary",
]


def _get_stock_info(inp: StockInfoInput):
    info = yf.Ticker(prices.yahoo_ticker(inp.tradingsymbol, inp.exchange)).info or {}
    out = {k: info[k] for k in INFO_KEYS if info.get(k) is not None}
    if not out:
        raise ValueError(f"No data found for {inp.tradingsymbol}")
    return out


def _query_database(inp: SqlInput):
    sql = inp.sql.strip().rstrip(";")
    if ";" in sql:
        raise ValueError("Only a single statement is allowed")
    # The agent_ro role is read-only with SELECT on the portfolio tables only.
    with agent_engine.connect() as conn:
        result = conn.execute(text(sql))
        cols = list(result.keys())
        rows = result.fetchmany(201)
    truncated = len(rows) > 200
    return {
        "columns": cols,
        "rows": [list(r) for r in rows[:200]],
        "truncated": truncated,
        "readable_tables": AGENT_READABLE_TABLES,
    }


HANDLERS = {
    "get_portfolio": (NoInput, _get_portfolio),
    "list_snapshots": (ListSnapshotsInput, _list_snapshots),
    "get_snapshot": (GetSnapshotInput, _get_snapshot),
    "get_price_history": (PriceHistoryInput, _get_price_history),
    "get_technicals": (TechnicalsInput, _get_technicals),
    "get_stock_info": (StockInfoInput, _get_stock_info),
    "query_database": (SqlInput, _query_database),
}


def run_tool(name: str, raw_input) -> tuple[str, bool]:
    """Execute a client tool. Returns (content, is_error)."""
    if name not in HANDLERS:
        return f"Unknown tool: {name}", True
    model, fn = HANDLERS[name]
    try:
        inp = model.model_validate(raw_input if isinstance(raw_input, dict) else {})
    except ValidationError as exc:
        return json.dumps({"INVALID_INPUT": exc.errors(include_url=False)}, default=str), True
    try:
        return json.dumps(fn(inp), default=str), False
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}", True
