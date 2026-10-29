"""Analyse orchestration: research agents in parallel, then strategy, then the risk gate.

    research (parallel, source-checked) ──► dossier ──► strategy ──► risk gate ──► report
                                                             ▲             │
                                                             └─ revise ────┘  (once)

The verdict in the published report is rendered here from the post-gate verdict, so a blocked "add" can
never reach the reader (or Slack) as "add". The run stops if its estimated cost passes the budget, and
every research agent has a deadline. Events are streamed in the shape `agent.run` uses, tagged with the
agent's name; only the final report is emitted as `text`, so it is the run's answer.
"""

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from ...config import get_settings
from ...db import session_scope
from ...models import AgentStep
from .. import agent
from ..events import Event
from . import research, risk
from .prompts import report_brief
from .schemas import Review, RuleResult, StrategyOutput, Subject, Verdict
from .strategy import Strategy, StrategyError

log = logging.getLogger(__name__)
settings = get_settings()

MAX_REVISIONS = 1


class Trace:
    """Best-effort record of every agent turn and result in `agent_steps` (never fails a run)."""

    def __init__(self, conversation_id: uuid.UUID):
        self.conversation_id = conversation_id

    async def save(self, name: str, kind: str, content: Any) -> None:
        await asyncio.to_thread(self._save, name, kind, content)

    def _save(self, name: str, kind: str, content: Any) -> None:
        try:
            with session_scope() as db:
                db.add(AgentStep(conversation_id=self.conversation_id, agent=name, kind=kind, content=content))
        except Exception:
            log.exception("could not save agent step")

    def logger(self, name: str) -> agent.OnAppend:
        async def log_turn(turn: dict) -> None:
            await self.save(name, "turn", turn)

        return log_turn


@dataclass
class Spend:
    """Running cost and activity of a pipeline run."""

    budget_usd: float
    cost_usd: float = 0.0
    tool_calls: int = 0
    by_agent: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    def add(self, event: Event) -> None:
        if event["type"] == "usage":
            cost = event["cost_usd"]
            self.cost_usd += cost
            self.by_agent[event.get("agent", "?")] += cost
        elif event["type"] == "tool_input":
            self.tool_calls += 1

    @property
    def over(self) -> bool:
        return self.cost_usd > self.budget_usd


def render_verdict(final: Verdict, proposed: Verdict, override: list[str]) -> str:
    lines = ["## Verdict", f"**{final.action.upper()}** · {final.confidence} confidence", "", final.summary]
    if override:
        lines += [
            "",
            f"> ⛔ **Risk override.** The strategy agent proposed **{proposed.action.upper()}** "
            f"({proposed.confidence} confidence); the risk agent changed it: " + " ".join(override),
            "",
            f"**The strategy agent's case for {proposed.action.upper()}** (not the published verdict): "
            + proposed.summary,
        ]
    lines += ["", *[f"- {r}" for r in proposed.rationale], "", "**What would change the view**"]
    lines += [f"- {w}" for w in proposed.what_would_change]
    return "\n".join(lines)


def render_risk(rules: list[RuleResult], review: Review, revised: bool) -> str:
    icon = {"pass": "✅", "warn": "⚠️", "block": "⛔"}
    lines = ["## Risk check", *[f"- {icon[r.status]} **{r.rule}**: {r.detail}" for r in rules]]
    lines.append(f"- {'✅' if review.grounded else '⚠️'} **Grounding**: {review.summary}")
    lines += [f"  - _{c.claim}_: {c.problem}" for c in review.unsupported_claims]
    if revised:
        lines.append("- ↩️ The strategy agent revised its report once after the first risk check.")
    return "\n".join(lines)


def gate(proposed: Verdict, rules: list[RuleResult], review: Review) -> tuple[Verdict, list[str]]:
    """The verdict that may be published, and why it differs from the proposal (empty if it doesn't).

    A blocked rule turns an add into a hold. Unsupported claims turn any action into a low-confidence
    hold: neither buying nor selling should rest on claims the research does not support.
    """
    reasons = [r.detail for r in rules if r.status == "block"]
    action, confidence = proposed.action, proposed.confidence
    if reasons and action == "add":
        action = "hold"
    if not review.grounded:
        reasons.append("Some claims are not supported by the source-checked research.")
        action, confidence = "hold", "low"
    if not reasons:
        return proposed, []
    summary = (
        f"Hold: the proposed {proposed.action} did not pass the risk check."
        if action != proposed.action
        else proposed.summary
    )
    return proposed.model_copy(update={"action": action, "confidence": confidence, "summary": summary}), reasons


async def analyze(s: Subject, conversation_id: uuid.UUID, on_append: agent.OnAppend) -> AsyncGenerator[Event, None]:
    """Run the pipeline, streaming events. The final report is appended to the conversation."""
    if not settings.agent_configured:
        yield {"type": "error", "message": "ANTHROPIC_API_KEY is not set"}
        return
    started = time.monotonic()
    trace = Trace(conversation_id)
    spend = Spend(settings.analysis_budget_usd)

    # 1. Research agents in parallel, alongside loading the position.
    team = research.team(s)
    yield {"type": "stage", "agent": "research", "label": f"{len(team)} research agents working in parallel"}
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tasks = [asyncio.create_task(research.run(r, trace.logger(r.name), queue.put)) for r in team]
    ctx_task = asyncio.create_task(risk.load_context(s))
    try:
        pending: set[asyncio.Task] = set(tasks)
        while pending or not queue.empty():
            if queue.empty():
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(pending | {getter}, return_when=asyncio.FIRST_COMPLETED)
                pending -= done
                if getter not in done:
                    getter.cancel()
                    continue
                event = getter.result()
            else:
                event = queue.get_nowait()
            spend.add(event)
            yield event
            if spend.over:
                yield {"type": "error", "message": f"Stopped: estimated cost passed the ${spend.budget_usd:g} budget."}
                return
        results = [t.result() for t in tasks]
        ctx = await ctx_task
    finally:  # on budget stop, error or cancellation, stop every agent still running
        for t in [*tasks, ctx_task]:
            t.cancel()

    for res in results:
        title = res.researcher.title.capitalize()
        if res.findings:
            extra = f" ({len(res.dropped)} discarded: source never retrieved)" if res.dropped else ""
            yield {
                "type": "note",
                "agent": res.researcher.name,
                "text": f"🔍 {title}: {res.fact_count} source-checked facts{extra}.",
            }
        else:
            yield {"type": "note", "agent": res.researcher.name, "text": f"⚠️ {title} failed: {res.error}"}
    if not any(res.findings for res in results):
        yield {"type": "error", "message": "All research agents failed; nothing to analyse."}
        return
    dossier = research.render_dossier(results)
    await trace.save(
        "research",
        "result",
        {
            "dossier": dossier,
            "dropped": {res.researcher.name: [f.model_dump() for f in res.dropped] for res in results},
            "errors": {res.researcher.name: res.error for res in results if res.error},
        },
    )
    position = json.dumps(
        {
            "position": ctx.position,
            "sector": ctx.sector,
            "sector_weight_pct": ctx.sector_weight,
            "is_etf": ctx.etf,
            "risk_limits_pct": {
                "max_position": settings.risk_max_position_pct,
                "max_sector": settings.risk_max_sector_pct,
            },
        },
        default=str,
        indent=1,
    )

    # 2. Strategy, then 3. the risk gate; one revision if the gate rejects the first draft.
    strategy = Strategy(
        f"Analyse {s.label}, which I hold.\n\n<position>\n{position}\n</position>\n\n"
        f"<research_dossier>\n{dossier}\n</research_dossier>\n\n{report_brief(s.kind)}",
        trace.logger("strategy"),
    )
    attempt = 0
    while True:
        yield {
            "type": "stage",
            "agent": "strategy",
            "label": "Strategy agent revising the report" if attempt else "Strategy agent writing the report",
        }
        try:
            async for e in strategy.write():
                spend.add(e)
                yield e
        except StrategyError as exc:
            yield {"type": "error", "message": f"Strategy agent failed: {exc}"}
            return
        out: StrategyOutput = strategy.output  # type: ignore[assignment]

        yield {"type": "stage", "agent": "risk", "label": "Risk agent checking limits and sources"}
        rules = risk.check_rules(out.verdict, ctx, s.kind)
        try:
            review, usage = await risk.review_grounding(
                dossier, position, f"{out.report}\n\n{render_verdict(out.verdict, out.verdict, [])}"
            )
        except Exception as exc:
            log.exception("grounding review failed")
            yield {"type": "error", "message": f"Risk check failed, so nothing was published: {exc}"}
            return
        usage["agent"] = "risk"
        spend.add(usage)
        yield usage
        blocked = [r for r in rules if r.status == "block"]
        passed = not blocked and review.grounded
        await trace.save(
            "risk",
            "result",
            {
                "attempt": attempt,
                "verdict": out.verdict.model_dump(),
                "rules": [r.model_dump() for r in rules],
                "review": review.model_dump(),
                "passed": passed,
            },
        )
        if spend.over:
            yield {"type": "error", "message": f"Stopped: estimated cost passed the ${spend.budget_usd:g} budget."}
            return
        if passed or attempt == MAX_REVISIONS:
            break
        problems = [f"- {r.rule}: {r.detail}" for r in blocked]
        problems += [f"- Unsupported: {c.claim} ({c.problem})" for c in review.unsupported_claims]
        yield {"type": "note", "agent": "risk", "text": "↩️ Risk agent sent the report back:\n" + "\n".join(problems)}
        strategy.revise(
            "The risk agent rejected this report:\n" + "\n".join(problems) + "\n\nRewrite the full report and "
            "verdict so they respect the limits and only make claims the dossier supports."
        )
        attempt += 1

    final, override = gate(out.verdict, rules, review)
    yield {
        "type": "note",
        "agent": "risk",
        "text": "✅ Risk check passed." if passed else "⛔ Risk agent changed the verdict: " + " ".join(override),
    }

    checked = sum(r.fact_count for r in results)
    dropped = sum(len(r.dropped) for r in results)
    elapsed = int(time.monotonic() - started)
    footer = (
        f"_{sum(1 for r in results if r.findings)} research agents ({checked} source-checked facts"
        + (f", {dropped} discarded" if dropped else "")
        + f"), a strategy agent and a risk agent · {spend.tool_calls} tool calls · "
        f"est. ${spend.cost_usd:.2f} · {elapsed // 60}m {elapsed % 60:02d}s._"
    )
    report = "\n\n".join(
        [
            out.report.strip(),
            render_verdict(final, out.verdict, override),
            render_risk(rules, review, revised=attempt > 0),
            footer,
        ]
    )
    await trace.save(
        "pipeline",
        "result",
        {
            "cost_usd": spend.cost_usd,
            "by_agent": dict(spend.by_agent),
            "tool_calls": spend.tool_calls,
            "seconds": elapsed,
            "verdict": final.model_dump(),
            "proposed": out.verdict.model_dump(),
        },
    )

    await on_append({"role": "assistant", "content": [{"type": "text", "text": report}]})
    yield {"type": "text", "agent": "report", "text": report}
    yield {"type": "done", "stop_reason": "end_turn"}
