"""Eval: does the risk agent's grounding review catch fabricated claims, without flagging clean reports?

Takes a real research dossier and the market sections of the report written from it (a public fixture:
research agents never see the portfolio, and position-specific sections are removed), plants fabrications
into copies of the report, and runs the grounding review on each case:

- changed figures: a number that appears in both report and dossier is altered in the report, either
  blatantly (leading digit) or subtly (last digit, e.g. 12.9% -> 12.0%)
- invented events: a plausible, sourced-looking claim the dossier does not contain is added
- clean: the report unchanged, repeated to estimate the false-positive rate

A fabrication counts as caught when the review is not grounded and one of its flagged claims contains the
planted text. Runs every fixture in evals/fixtures by default, `--runs` times each (the review is not
deterministic), and reports recall and false-positive rate with 95% Wilson intervals, so a small sample
is not mistaken for a strong result. Calls the Claude API (about $0.05 per case with the default model).

    docker compose run --rm -v "$PWD/apps/api:/src" -w /src api python -m evals.grounding
"""

import argparse
import asyncio
import json
import math
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from app.services.analysis import risk

FIXTURES = Path(__file__).parent / "fixtures"
FIGURE = re.compile(r"(?:₹\s?)?\d[\d,]*(?:\.\d+)?\s?(?:%|cr|mn|x)")
INVENTED = [
    (
        "- **Buyback:** the board approved a ₹2,500 cr share buyback at ₹5,200 per share on 27 Oct 2026 "
        "([Moneycontrol](https://www.moneycontrol.com/news/business/buyback-approved-2026.html)).",
        "buyback",
    ),
    (
        "- **Rating change:** Jefferies upgraded the stock to Buy with a ₹6,100 target on 29 Oct 2026 "
        "([ET Markets](https://economictimes.indiatimes.com/markets/stocks/news/jefferies-upgrade-2026.cms)).",
        "6,100",
    ),
    (
        "- **Regulation:** SEBI opened an inquiry into related-party lending at the finance arm on 21 Oct 2026 "
        "([Business Standard](https://www.business-standard.com/markets/news/sebi-inquiry-2026.html)).",
        "SEBI",
    ),
]


@dataclass
class Case:
    name: str
    report: str
    marker: str | None  # planted text that must appear in a flagged claim; None for a clean report


def alter(figure: str, subtle: bool = False) -> str:
    """Change the leading digit (blatant) or the last digit (subtle); the figure stays plausible but wrong."""
    digits = [i for i, ch in enumerate(figure) if ch.isdigit()]
    i = digits[-1] if subtle else digits[0]
    new = (int(figure[i]) + 1) % 10 if subtle else (int(figure[i]) + 3) % 9 + 1
    return figure[:i] + str(new) + figure[i + 1 :]


def build_cases(dossier: str, report: str, figures: int, subtle: int, repeats: int, seed: int) -> list[Case]:
    rng = random.Random(seed)
    shared = sorted(
        {m.group(0) for m in FIGURE.finditer(report) if m.group(0) in dossier and report.count(m.group(0)) == 1}
    )
    cases = [Case(f"clean #{i + 1}", report, None) for i in range(repeats)]
    picks = rng.sample(shared, min(figures + subtle, len(shared)))
    for n, fig in enumerate(picks):
        wrong = alter(fig, subtle=n >= figures)
        label = "subtle" if n >= figures else "figure"
        cases.append(Case(f"{label} {fig} -> {wrong}", report.replace(fig, wrong), wrong))
    for line, marker in INVENTED:
        cases.append(
            Case(
                f"invented: {marker}",
                report.replace("## Risks and catalysts", f"{line}\n\n## Risks and catalysts"),
                marker,
            )
        )
    return cases


def norm(text: str) -> str:
    return re.sub(r"[\s,]", "", text).lower()


# A synthetic position like the one production passes (the fixture reports contain no position figures).
POSITION = json.dumps(
    {
        "position": {"quantity": 40, "average_price": 1000.0, "weight": 3.2, "pnl_percentage": 12.5},
        "sector": "Technology",
        "sector_weight_pct": 9.0,
        "risk_limits_pct": {"max_position": 15, "max_sector": 35},
    }
)


@dataclass
class Tally:
    caught: int = 0
    fabricated: int = 0
    false_pos: int = 0
    clean: int = 0
    cost: float = 0.0


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


async def evaluate(fixture: Path, args: argparse.Namespace, tally: Tally) -> None:
    data = json.loads(fixture.read_text())
    cases = build_cases(data["dossier"], data["report"], args.figures, args.subtle, args.repeats, args.seed)
    # Public market data the report used from the live position row (e.g. today's move).
    position = json.dumps({**json.loads(POSITION), "market_today": data.get("context", {})})
    sem = asyncio.Semaphore(4)

    async def judge(case: Case):
        async with sem:
            return await risk.review_grounding(data["dossier"], position, case.report)

    for run in range(args.runs):
        results = await asyncio.gather(*(judge(c) for c in cases))
        print(f"\n{data['subject']}, run {run + 1}/{args.runs}")
        print(f"{'case':<40} {'grounded':<9} {'result':<8} flagged")
        for case, (review, usage) in zip(cases, results, strict=True):
            tally.cost += usage["cost_usd"]
            flagged = [c.claim for c in review.unsupported_claims]
            if case.marker is None:
                tally.clean += 1
                ok = review.grounded
                tally.false_pos += not ok
            else:
                tally.fabricated += 1
                ok = not review.grounded and any(norm(case.marker) in norm(c) for c in flagged)
                tally.caught += ok
            print(f"{case.name:<40} {review.grounded!s:<9} {'ok' if ok else 'MISS':<8} {'; '.join(flagged)[:90]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixture", type=Path, action="append", help="default: every file in evals/fixtures")
    ap.add_argument("--figures", type=int, default=3, help="blatantly changed figures per run")
    ap.add_argument("--subtle", type=int, default=3, help="subtly changed figures per run")
    ap.add_argument("--repeats", type=int, default=2, help="clean cases per run")
    ap.add_argument("--runs", type=int, default=2, help="repetitions of the whole case set")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    tally = Tally()
    for fixture in args.fixture or sorted(FIXTURES.glob("*.json")):
        asyncio.run(evaluate(fixture, args, tally))
    recall_lo, recall_hi = wilson(tally.caught, tally.fabricated)
    fp_lo, fp_hi = wilson(tally.false_pos, tally.clean)
    print(
        f"\nrecall {tally.caught}/{tally.fabricated} (95% CI {recall_lo:.0%}-{recall_hi:.0%}), "
        f"false positives {tally.false_pos}/{tally.clean} (95% CI {fp_lo:.0%}-{fp_hi:.0%}), "
        f"est. cost ${tally.cost:.2f}"
    )
    ok = tally.fabricated and tally.caught / tally.fabricated >= 0.8 and tally.false_pos == 0
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
