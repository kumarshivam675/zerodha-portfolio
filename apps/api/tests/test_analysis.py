"""Analyse pipeline: risk rules, source verification, the gate and orchestration; no API or DB calls."""

import asyncio
import uuid

import pytest

from app.services.analysis import pipeline, research, risk
from app.services.analysis.prompts import report_brief
from app.services.analysis.schemas import (
    Fact,
    ResearchFindings,
    Review,
    StrategyOutput,
    Subject,
    Topic,
    UnsupportedClaim,
    Verdict,
    strict_schema,
)

EQUITY = Subject("INFY", "NSE", "equity")


def verdict(action="add", confidence="high", target=8.0) -> Verdict:
    return Verdict(
        action=action,
        confidence=confidence,
        target_weight_pct=target,
        summary="s",
        rationale=["r"],
        what_would_change=["w"],
    )


def ctx(weight=5.0, sector="Technology", sector_weight=20.0, etf=False, position=True, unclassified=()):
    return risk.PortfolioContext({"weight": weight} if position else None, sector, sector_weight, etf, unclassified)


def statuses(rules) -> dict[str, str]:
    return {r.rule: r.status for r in rules}


GROUNDED = Review(grounded=True, unsupported_claims=[], summary="ok")
UNGROUNDED = Review(grounded=False, unsupported_claims=[UnsupportedClaim(claim="x", problem="y")], summary="no")


# --- risk rules ----------------------------------------------------------------------------------


def test_add_within_limits_passes():
    assert set(statuses(risk.check_rules(verdict(), ctx(), "equity")).values()) == {"pass"}


@pytest.mark.parametrize(
    "v, c, rule",
    [
        (verdict(), ctx(weight=16), "Position limit"),
        (verdict(target=20), ctx(), "Position limit"),
        (verdict(target=None), ctx(), "Position limit"),  # an add must say how far
        (verdict(), ctx(sector_weight=40), "Sector limit"),
        (verdict(confidence="low"), ctx(), "Conviction"),
        (verdict(), ctx(sector=None, sector_weight=None), "Sector limit"),  # fails closed
        (verdict(), ctx(position=False), "Position limit"),  # fails closed
        (verdict(), ctx(sector_weight=None, unclassified=("XYZ",)), "Sector limit"),  # a peer unclassified
        (verdict(target=4.0), ctx(weight=5.0), "Position limit"),  # target below current weight
    ],
)
def test_add_is_blocked(v, c, rule):
    assert statuses(risk.check_rules(v, c, "equity"))[rule] == "block"


@pytest.mark.parametrize("action", ["hold", "trim", "exit"])
def test_only_add_can_be_blocked(action):
    rules = risk.check_rules(verdict(action, "low", None), ctx(weight=30, sector=None, sector_weight=None), "equity")
    assert "block" not in statuses(rules).values()


def test_holding_above_limit_warns():
    assert statuses(risk.check_rules(verdict("hold"), ctx(weight=20), "equity"))["Position limit"] == "warn"


@pytest.mark.parametrize("c, kind", [(ctx(weight=72, etf=True), "equity"), (ctx(weight=50), "mf")])
def test_diversified_holdings_skip_weight_limits(c, kind):
    assert statuses(risk.check_rules(verdict(), c, kind)) == {
        "Conviction": "pass",
        "Position and sector limits": "pass",
    }


def test_a_listed_unsupported_claim_means_not_grounded():
    contradictory = UNGROUNDED.model_copy(update={"grounded": True})
    assert risk.consistent(contradictory).grounded is False
    assert risk.consistent(GROUNDED).grounded is True


# --- the gate ------------------------------------------------------------------------------------


def test_gate_keeps_a_passing_verdict():
    v = verdict()
    assert pipeline.gate(v, risk.check_rules(v, ctx(), "equity"), GROUNDED) == (v, [])


def test_gate_turns_a_blocked_add_into_hold():
    v = verdict()
    final, reasons = pipeline.gate(v, risk.check_rules(v, ctx(weight=20), "equity"), GROUNDED)
    assert final.action == "hold" and reasons


@pytest.mark.parametrize("action", ["add", "trim", "exit"])
def test_unsupported_claims_force_a_low_confidence_hold(action):
    final, reasons = pipeline.gate(verdict(action, "high"), [], UNGROUNDED)
    assert (final.action, final.confidence) == ("hold", "low") and reasons
    assert final.summary.startswith(f"Hold: the proposed {action}")


def test_rendered_verdict_shows_override():
    final, reasons = pipeline.gate(verdict(), risk.check_rules(verdict(), ctx(weight=20), "equity"), GROUNDED)
    text = pipeline.render_verdict(final, verdict(), reasons)
    assert text.startswith("## Verdict\n**HOLD**") and "proposed **ADD**" in text
    assert "The strategy agent's case for ADD** (not the published verdict)" in text


# --- source verification -------------------------------------------------------------------------


def test_normalize_url():
    assert research.normalize_url("https://www.Example.com/a/?utm_source=x&id=2#frag") == "example.com/a?id=2"


def test_verify_keeps_only_retrieved_sources():
    turns = [
        {"role": "assistant", "content": [
            {"type": "web_search_tool_result", "content": [{"type": "web_search_result", "url": "https://a.com/x"}]},
            {"type": "web_fetch_tool_result", "content": {"type": "web_fetch_result", "url": "https://b.com/y/"}},
            {"type": "tool_use", "id": "t1", "name": "get_technicals", "input": {}},
            {"type": "tool_use", "id": "t2", "name": "get_stock_info", "input": {}},
            {"type": "text", "text": "see https://invented.com/z"},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": '{"rsi": 78.74, "return_3m": 4.98}'},
            {"type": "tool_result", "tool_use_id": "t2", "content": "boom", "is_error": True},
        ]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t3", "name": "submit_findings", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t3", "content": "Findings recorded."}]},
    ]  # fmt: skip
    sources = research.retrieved_sources(turns)
    assert sources.urls == {"a.com/x", "b.com/y"}
    assert set(sources.tool_values) == {"get_technicals"}  # failed calls and submit_findings are not sources

    facts = [Fact(claim=c, as_of="2026-10-01", source=src) for c, src in [
        ("searched", "http://www.a.com/x"), ("fetched", "https://b.com/y"),
        ("RSI is 78.7, 3M return +5.0%", "tool:get_technicals"),  # rounded tool figures
        ("12 points above its peers", "tool:get_technicals"),  # a figure the tool never returned
        ("invented", "https://invented.com/z"), ("failed tool", "tool:get_stock_info"),
        ("self-cited", "tool:submit_findings"),
    ]]  # fmt: skip
    checked = research.verify(ResearchFindings(topics=[Topic(heading="h", facts=facts)], gaps=[]), sources)
    assert [f.claim for f in checked.findings.topics[0].facts] == [
        "searched", "fetched", "RSI is 78.7, 3M return +5.0%", "12 points above its peers",
    ]  # fmt: skip
    assert [f.claim for f in checked.dropped] == ["invented", "failed tool", "self-cited"]
    assert checked.computed == {"12 points above its peers"}


def test_computed_facts_are_labelled_in_the_dossier():
    fact = Fact(claim="12 points above its peers", as_of="2026-10-01", source="tool:get_technicals")
    res = research.ResearchResult(
        research.team(EQUITY)[1],
        ResearchFindings(topics=[Topic(heading="h", facts=[fact])], gaps=[]),
        computed={fact.claim},
    )
    assert "figures computed by the agent from this tool's data" in research.render_dossier([res])


# --- team, prompts, schemas ----------------------------------------------------------------------


def tool_names(r) -> set[str]:
    return {t["name"] for t in r.tools}


def test_equity_team_has_scoped_tools():
    team = {r.name: r for r in research.team(EQUITY)}
    assert set(team) == {"company", "technicals", "macro"}
    assert tool_names(team["technicals"]) == {"get_technicals", "get_price_history"}  # no web
    assert "get_portfolio" not in set().union(*map(tool_names, team.values()))  # research never sees the position


def test_fund_team_and_brief_drop_technicals():
    assert [r.name for r in research.team(Subject("INF0", "NSE", "mf", "Fund"))] == ["fund", "macro"]
    assert "## Technical picture" not in report_brief("mf") and "## Technical picture" in report_brief("equity")
    assert "## Verdict" not in report_brief("equity")  # the verdict is structured and rendered after the gate


def test_strict_schema_closes_every_object():
    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for v in node.values():
                yield from objects(v)
        elif isinstance(node, list):
            for v in node:
                yield from objects(v)

    found = list(objects(strict_schema(ResearchFindings)))
    assert len(found) == 3 and all(o["additionalProperties"] is False for o in found)


# --- orchestration -------------------------------------------------------------------------------


@pytest.fixture
def stubbed(monkeypatch):
    """Stub every agent and external call. `state` steers what the strategy proposes per attempt."""
    state = {"verdicts": [verdict("hold", target=None)], "reviews": [GROUNDED], "attempts": 0, "fail": set()}

    async def fake_research(r, log_turn, emit):
        await emit({"type": "tool_input", "id": r.name, "name": "web_search", "input": {}, "agent": r.name})
        await emit({"type": "usage", "cost_usd": 0.1, "agent": r.name})
        if r.name in state["fail"]:
            return research.ResearchResult(r, error="boom")
        fact = Fact(claim=f"fact from {r.name}", as_of="2026-10-01", source="https://a.com")
        return research.ResearchResult(r, ResearchFindings(topics=[Topic(heading="h", facts=[fact])], gaps=[]))

    async def fake_write(self):
        state["attempts"] += 1
        yield {"type": "usage", "cost_usd": 0.2, "agent": "strategy"}
        v = state["verdicts"][min(state["attempts"], len(state["verdicts"])) - 1]
        self.output = StrategyOutput(report=f"## Snapshot\nreport v{state['attempts']}", verdict=v)

    async def fake_review(dossier, position, report):
        assert "fact from" in dossier
        rv = state["reviews"][min(state["attempts"], len(state["reviews"])) - 1]
        return rv, {"type": "usage", "cost_usd": 0.05}

    async def fake_ctx(subject):
        return ctx()

    async def no_save(self, *a):
        return None

    monkeypatch.setattr(pipeline.settings, "anthropic_api_key", "test")
    monkeypatch.setattr(pipeline.settings, "analysis_budget_usd", 4.0)
    monkeypatch.setattr(research, "run", fake_research)
    monkeypatch.setattr(pipeline.Strategy, "write", fake_write)
    monkeypatch.setattr(risk, "review_grounding", fake_review)
    monkeypatch.setattr(risk, "load_context", fake_ctx)
    monkeypatch.setattr(pipeline.Trace, "save", no_save)
    return state


def run_pipeline():
    saved: list[dict] = []

    async def on_append(turn):
        saved.append(turn)

    async def collect():
        return [e async for e in pipeline.analyze(EQUITY, uuid.uuid4(), on_append)]

    return asyncio.run(collect()), saved


def final_report(events) -> str:
    texts = [e for e in events if e["type"] == "text"]
    assert len(texts) == 1 and texts[0]["agent"] == "report"  # only the final report is the answer
    return texts[0]["text"]


def test_report_published_after_gate_passes(stubbed):
    events, saved = run_pipeline()
    report = final_report(events)
    assert "report v1" in report and "## Verdict\n**HOLD**" in report and "## Risk check" in report
    assert (
        "3 research agents (3 source-checked facts)" in report and "3 tool calls" in report and "est. $0.55" in report
    )
    assert saved == [{"role": "assistant", "content": [{"type": "text", "text": report}]}]
    assert events[-1]["type"] == "done" and stubbed["attempts"] == 1


def test_rejected_report_is_revised_once(stubbed):
    stubbed["verdicts"] = [verdict(confidence="low"), verdict("hold", target=None)]
    events, _ = run_pipeline()
    report = final_report(events)
    assert stubbed["attempts"] == 2 and "report v2" in report and "revised its report once" in report
    assert "Risk override" not in report


def test_blocked_add_is_published_as_hold(stubbed):
    stubbed["verdicts"] = [verdict(confidence="low")] * 2
    report = final_report(run_pipeline()[0])
    assert "## Verdict\n**HOLD**" in report and "proposed **ADD**" in report


def test_partial_research_failure_still_reports(stubbed):
    stubbed["fail"] = {"macro"}
    events, _ = run_pipeline()
    assert any(e["type"] == "note" and "failed: boom" in e["text"] for e in events)
    assert "2 research agents" in final_report(events)


def test_budget_stops_the_run(stubbed, monkeypatch):
    monkeypatch.setattr(pipeline.settings, "analysis_budget_usd", 0.15)
    events, saved = run_pipeline()
    assert events[-1]["type"] == "error" and "budget" in events[-1]["message"] and not saved
