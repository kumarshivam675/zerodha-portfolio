"""The risk gate: hard portfolio limits in plain code, plus a grounding review by Claude.

The limits fail closed: an "add" is blocked whenever the data needed to check it is missing.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal

import yfinance as yf

from ...config import get_settings
from ...db import session_scope
from .. import agent, portfolio, prices
from ..events import UsageEvent
from .prompts import REVIEW_SYSTEM
from .schemas import Review, RuleResult, Subject, Verdict

log = logging.getLogger(__name__)
settings = get_settings()

_classification: dict[tuple[str, str], tuple[str | None, bool]] = {}  # successful lookups only
# Yahoo lookups are slow network calls; a small dedicated pool keeps them from starving the default
# executor that database writes and tools run on.
_yahoo = ThreadPoolExecutor(max_workers=4, thread_name_prefix="yahoo")


@dataclass(frozen=True)
class PortfolioContext:
    position: dict | None  # the holding's row from current_portfolio(), with its weight
    sector: str | None
    sector_weight: float | None  # % of the portfolio in the holding's sector; None if not known for sure
    etf: bool
    unclassified: tuple[str, ...] = ()  # holdings whose sector lookup failed


def _classify(tradingsymbol: str, exchange: str) -> tuple[str | None, bool] | None:
    """(sector, is_etf) from Yahoo Finance, or None if the lookup failed (not cached, so it is retried)."""
    key = (tradingsymbol, exchange)
    if key not in _classification:
        try:
            info = yf.Ticker(prices.yahoo_ticker(tradingsymbol, exchange)).info or {}
        except Exception:
            log.warning("could not classify %s:%s", exchange, tradingsymbol)
            return None
        if not info:
            return None
        _classification[key] = (info.get("sector"), info.get("quoteType") == "ETF")
    return _classification[key]


async def load_context(s: Subject) -> PortfolioContext:
    """The investor's position in `s` plus the sector exposure the limits need."""

    def load() -> dict:
        with session_scope() as db:
            return portfolio.current_portfolio(db)

    holdings = (await asyncio.to_thread(load))["holdings"]
    pos = next((h for h in holdings if h["tradingsymbol"] == s.tradingsymbol), None)
    if s.kind != "equity" or not pos:
        return PortfolioContext(pos, None, None, etf=False)

    equities = [h for h in holdings if h["kind"] == "equity"]
    loop = asyncio.get_running_loop()
    try:
        classes = await asyncio.wait_for(
            asyncio.gather(
                *(loop.run_in_executor(_yahoo, _classify, h["tradingsymbol"], h["exchange"]) for h in equities)
            ),
            45,
        )
    except TimeoutError:
        return PortfolioContext(pos, None, None, etf=False, unclassified=("(lookup timed out)",))
    by_symbol = dict(zip((h["tradingsymbol"] for h in equities), classes, strict=True))
    own = by_symbol.get(s.tradingsymbol)
    etf = bool(own and own[1])
    unclassified = tuple(sym for sym, c in by_symbol.items() if c is None)
    if own is None or own[0] is None:
        return PortfolioContext(pos, None, None, etf, unclassified)
    sector = own[0]
    # Fail closed: one unclassified holding could be in this sector, so the total is not known.
    weight = None
    if not unclassified:
        weight = sum(h["weight"] for h in equities if (c := by_symbol[h["tradingsymbol"]]) and c[0] == sector)
    return PortfolioContext(pos, sector, weight, etf, unclassified)


def check_rules(verdict: Verdict, ctx: PortfolioContext, kind: str) -> list[RuleResult]:
    """Hard limits. Only "add" can be blocked: holding, trimming and exiting never increase risk."""
    max_pos, max_sector = settings.risk_max_position_pct, settings.risk_max_sector_pct
    adding = verdict.action == "add"
    out: list[RuleResult] = []

    def rule(name: str, status: Literal["pass", "warn", "block"], detail: str) -> None:
        out.append(RuleResult(rule=name, status=status, detail=detail))

    if adding and verdict.confidence == "low":
        rule("Conviction", "block", "No adding on low confidence.")
    else:
        rule("Conviction", "pass", f"{verdict.confidence.capitalize()} confidence.")

    if kind != "equity" or ctx.etf:
        what = "ETFs" if ctx.etf else "mutual funds"
        rule("Position and sector limits", "pass", f"Not applied to {what} (diversified by design).")
        return out

    if ctx.position is None:
        rule("Position limit", "block" if adding else "warn", "Position not found in current holdings.")
        return out
    weight, target = ctx.position["weight"], verdict.target_weight_pct
    if adding and weight >= max_pos:
        rule("Position limit", "block", f"Already {weight:.1f}% of the portfolio; the limit is {max_pos:g}%.")
    elif adding and target is not None and target > max_pos:
        rule("Position limit", "block", f"Target weight {target:g}% is above the {max_pos:g}% limit.")
    elif adding and target is None:
        rule("Position limit", "block", "An add must state the weight to add up to.")
    elif adding and target is not None and target <= weight:
        rule("Position limit", "block", f"Target weight {target:g}% is not above the current {weight:.1f}%.")
    elif weight > max_pos and verdict.action == "hold":
        rule("Position limit", "warn", f"{weight:.1f}% of the portfolio, above the {max_pos:g}% limit.")
    else:
        rule("Position limit", "pass", f"{weight:.1f}% of the portfolio (limit {max_pos:g}%).")

    if ctx.sector is None or ctx.sector_weight is None:
        why = (
            f"could not classify {', '.join(ctx.unclassified[:5])}"
            if ctx.sector and ctx.unclassified
            else "the holding's sector is unknown"
        )
        rule("Sector limit", "block" if adding else "warn", f"Sector exposure not known ({why}).")
    elif adding and ctx.sector_weight >= max_sector:
        rule(
            "Sector limit",
            "block",
            f"{ctx.sector} is already {ctx.sector_weight:.1f}% of the portfolio; the limit is {max_sector:g}%.",
        )
    else:
        status: Literal["pass", "warn"] = "pass" if ctx.sector_weight <= max_sector else "warn"
        rule(
            "Sector limit",
            status,
            f"{ctx.sector} is {ctx.sector_weight:.1f}% of the portfolio (limit {max_sector:g}%).",
        )
    return out


async def review_grounding(dossier: str, position: str, report: str) -> tuple[Review, UsageEvent]:
    """Claude checks every material claim in the report (and verdict) against the source-checked dossier."""
    started = time.monotonic()
    msg = await agent.client().messages.parse(
        model=settings.risk_model,
        max_tokens=16000,
        system=REVIEW_SYSTEM,
        output_config={"effort": "medium"},
        messages=[
            {
                "role": "user",
                "content": f"<dossier>\n{dossier}\n</dossier>\n<position>\n{position}\n</position>\n"
                f"<report>\n{report}\n</report>",
            }
        ],
        output_format=Review,
    )
    if msg.stop_reason == "refusal" or msg.parsed_output is None:
        raise RuntimeError(f"Grounding review not returned (stop reason: {msg.stop_reason})")
    return consistent(msg.parsed_output), agent.usage_event(msg, started)


def consistent(review: Review) -> Review:
    """The reviewer lists only material problems, so a listed claim means not grounded, even when the
    model's own flag disagrees (seen in evals: a claim listed, grounded=true)."""
    if review.unsupported_claims and review.grounded:
        return review.model_copy(update={"grounded": False})
    return review
