# Evals

Unit tests (`tests/`) pin down the code paths with Claude stubbed or replayed from recorded responses.
These evals measure the model-driven parts against the real API, so they cost a little money and
are run by hand, not in CI.

## Grounding review (`evals/grounding.py`)

Can the risk agent's grounding review catch fabricated claims in a strategy report, without flagging
a clean one? The fixture is a real research dossier plus the market sections of the report written
from it. It is public data only: research agents never see the portfolio, and the position sections
are removed. The eval plants fabrications into copies of the report:

| Case | What is planted |
|---|---|
| figure | a figure shared with the dossier changed blatantly (`+44%` → `+84%`) |
| subtle | the same, last digit only (`+44%` → `+45%`, `1.9%` → `1.0%`) |
| invented | a plausible, sourced-looking event the dossier does not contain (buyback, rating change, regulator) |
| clean | the report unchanged, to measure false positives |

A fabrication counts as caught only if the review is not grounded **and** a flagged claim contains the
planted text. The eval fails if recall is below 80% or there is any false positive.

```bash
docker compose run --rm --no-deps -v "$PWD/apps/api:/src" -w /src --entrypoint python api -m evals.grounding
```

Results with `RISK_MODEL=claude-opus-5-5`, 29 Oct 2026, one run per fixture. Each run plants 3 blatant
figures, 3 subtle figures and 3 invented events, plus 2 clean copies. The fixtures are INFY and HDFCBANK
analyses, chosen because they are ordinary large caps, not for any position.

| Planted | Caught |
|---|---|
| Blatant figure change (`3.3%` → `7.3%`, `120%` → `520%`) | 6/6 |
| Invented, sourced-looking event (buyback, rating change, regulator inquiry) | 6/6 |
| Subtle figure change, last digit only (`10.27%` → `10.28%`, `1.8%` → `1.9%`) | 2/6 |
| Clean report, wrongly flagged | 0/4 |

**What this means.** The grounding review reliably catches invented events and materially wrong
figures. It does not reliably catch a last-digit slip, so treat it as a filter for fabrication, not
for transcription accuracy. That is one reason every fact keeps a link to its source. With this few
cases the numbers are indicative (the eval prints 95% Wilson intervals), not a guarantee.

Two lessons from building the fixtures:

- **A clean baseline has to really be clean.** Sentences that depend on the live position (today's
  move, your sector weight) contradict the eval's synthetic position and show up as false positives,
  so they are removed from fixtures.
- **The production review has missed things.** While building an earlier fixture, a published report
  called a regulatory hit "confirmed", although its own research marked it unverified, and the review
  passed it. Since then, a claim the reviewer lists always counts as unsupported (`risk.consistent`):
  in evals the reviewer sometimes listed a claim but still answered `grounded: true`.

## Source provenance (`evals/provenance.py`)

This one is free: it makes no API calls. It re-runs the research source check over every Analyse run
stored in your database and reports, per agent, how many facts were kept, how many were discarded
(their source was never retrieved), and how many were labelled as computed (tool figures that are not
in the tool's output). Typically almost nothing is discarded, while a large share of the technicals
agent's facts are computed: it derives gaps, distances and ratios rather than quoting the tool, which
is why those facts are labelled.

```bash
docker compose run --rm --no-deps -v "$PWD/apps/api:/src" -w /src --entrypoint python api -m evals.provenance
```
