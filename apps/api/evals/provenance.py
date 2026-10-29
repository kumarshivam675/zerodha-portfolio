"""Eval: what the research source check kept, discarded and labelled as computed, across stored runs.

Re-runs research.verify on every Analyse run in the database (agent_steps), from the findings each
research agent submitted and the sources it actually retrieved. Costs nothing: no API calls. Use it to
spot agents that cite links they never opened, or present computed figures as tool data.

    docker compose run --rm -v "$PWD/apps/api:/src" -w /src api python -m evals.provenance
"""

from collections import defaultdict

from sqlalchemy import select

from app.db import session_scope
from app.models import AgentStep
from app.services.analysis import research
from app.services.analysis.schemas import ResearchFindings


def main() -> None:
    turns: dict[tuple, list[dict]] = defaultdict(list)
    with session_scope() as db:
        for step in db.scalars(select(AgentStep).where(AgentStep.kind == "turn").order_by(AgentStep.id)):
            if step.agent not in ("strategy", "risk", "research", "pipeline") and isinstance(step.content, dict):
                turns[(step.conversation_id, step.agent)].append(step.content)

    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])  # runs, kept, dropped, computed
    for (_, name), history in turns.items():
        submitted = [
            b["input"]
            for t in history
            if t["role"] == "assistant"
            for b in t["content"]
            if b.get("type") == "tool_use" and b.get("name") == research.SUBMIT_TOOL["name"]
        ]
        if not submitted:
            continue
        checked = research.verify(ResearchFindings.model_validate(submitted[-1]), research.retrieved_sources(history))
        kept = sum(len(t.facts) for t in checked.findings.topics)
        row = totals[name]
        row[0] += 1
        row[1] += kept
        row[2] += len(checked.dropped)
        row[3] += len(checked.computed)

    print(f"{'agent':<12} {'runs':>5} {'kept':>6} {'dropped':>8} {'computed':>9} {'dropped %':>10}")
    for name, (runs, kept, dropped, computed) in sorted(totals.items()):
        share = dropped / (kept + dropped) if kept + dropped else 0.0
        print(f"{name:<12} {runs:>5} {kept:>6} {dropped:>8} {computed:>9} {share:>10.0%}")


if __name__ == "__main__":
    main()
