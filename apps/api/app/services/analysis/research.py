"""Research agents: gather dated facts with sources, then check each source in code.

A research agent reports through the `submit_findings` tool, so its output is schema-validated rather
than parsed from prose. Then, before the strategy agent sees anything:

- A fact citing a URL is kept only if this agent's own web_search/web_fetch calls returned that URL.
  This proves provenance, not content: it stops invented links, but a wrong figure attributed to a
  real page passes here and is left to the risk agent's grounding review and the reader.
- A fact citing a tool is kept only if the agent called that tool successfully. If the fact's figures
  also appear in that tool's output it is tool data; otherwise it is labelled as computed by the agent.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from pydantic import ValidationError

from ...config import get_settings
from .. import agent
from ..agent_tools import run_tool, toolset
from ..events import Event
from .prompts import RESEARCH_SYSTEM
from .schemas import Fact, ResearchFindings, Subject, strict_schema

log = logging.getLogger(__name__)
settings = get_settings()

SUBMIT_TOOL = {
    "name": "submit_findings",
    "description": "Submit your research findings. Call exactly once, when your research is complete; this ends "
    "your task.",
    "input_schema": strict_schema(ResearchFindings),
    "strict": True,
}
NUDGE = "You have not submitted your findings. Call submit_findings now with what you found."

Emit = Callable[[Event], Awaitable[None]]


@dataclass(frozen=True)
class Researcher:
    name: str
    title: str
    task: str
    tools: list[dict] = field(default_factory=list)


@dataclass
class ResearchResult:
    researcher: Researcher
    findings: ResearchFindings | None = None
    dropped: list[Fact] = field(default_factory=list)
    computed: set[str] = field(default_factory=set)
    error: str | None = None

    @property
    def fact_count(self) -> int:
        return sum(len(t.facts) for t in self.findings.topics) if self.findings else 0


def team(s: Subject) -> list[Researcher]:
    """The research agents for a holding. Only the technicals agent reads market data tools; none see the portfolio."""
    macro = Researcher(
        "macro",
        "macro and geopolitical research",
        f"Find the market-wide and geopolitical factors that could move {s.label}: sector news, commodity and "
        "input costs, currency, interest rates, RBI/Fed policy, government policy and budget items, trade/tariff "
        "and geopolitical developments, and global peers. For each, note the likely direction of impact.",
        toolset(web_search=8, web_fetch=4),
    )
    if s.kind == "mf":
        return [
            Researcher(
                "fund",
                "fund research",
                f"Research {s.label}. Find its latest factsheet: category, benchmark, AUM, expense ratio, top "
                "holdings, sector mix, fund manager, and 3M/6M/1Y returns versus its benchmark and category. "
                "Also note any changes in the last 6 months (manager, mandate, flows, ratings).",
                toolset(web_search=10, web_fetch=6),
            ),
            macro,
        ]
    sym, ex = s.tradingsymbol, s.exchange
    return [
        Researcher(
            "company",
            "company and news research",
            f"Research {s.label}. Call get_stock_info for {sym} on {ex}, then search the web for the last 6 months: "
            "quarterly results and guidance, management commentary, orders/contracts, corporate actions, broker "
            "rating changes and target prices, promoter/FII/DII holding changes, and regulatory developments. "
            "Include valuation versus sector peers.",
            toolset("get_stock_info", web_search=12, web_fetch=6),
        ),
        Researcher(
            "technicals",
            "technical and price research",
            f"Build the price and technical picture for {sym} on {ex}. Call get_technicals for {sym}, and for "
            "NIFTYBEES on NSE as the Nifty 50 benchmark. Use get_price_history if you need the 3-month and 6-month "
            "moves or support/resistance levels. Report: returns over 1M/3M/6M/1Y versus the benchmark, trend "
            "versus 20/50/200-day averages, RSI, volatility, max drawdown, distance from 52-week high/low, likely "
            "support and resistance levels, and the volume trend.",
            toolset("get_technicals", "get_price_history"),
        ),
        macro,
    ]


# --- source verification -------------------------------------------------------------------------


def normalize_url(url: str) -> str:
    """Comparable form of a URL: no scheme, `www.`, fragment, tracking parameters or trailing slash."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")])
    return f"{host}{parts.path.rstrip('/')}" + (f"?{query}" if query else "")


NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers(text: str) -> list[str]:
    """Figures in `text` worth checking: anything with a decimal point or at least two digits."""
    return [n.replace(",", "") for n in NUMBER.findall(text) if "." in n or len(n.replace(",", "")) >= 2]


def figure_in(figure: str, values: set[float]) -> bool:
    """Whether `figure` matches one of `values` when rounded to the figure's precision."""
    decimals = len(figure.split(".")[1]) if "." in figure else 0
    x = float(figure)
    return any(abs(round(v, decimals) - x) < 1e-9 or abs(abs(round(v, decimals)) - x) < 1e-9 for v in values)


@dataclass
class Sources:
    """What an agent actually retrieved: URLs from web search/fetch, and each successful tool's output."""

    urls: set[str] = field(default_factory=set)
    tool_values: dict[str, set[float]] = field(default_factory=dict)  # tool name -> numbers it returned


def retrieved_sources(turns: list[dict]) -> Sources:
    """The sources across an agent's turns. The findings tool itself is not a source."""
    sources = Sources()
    urls = sources.urls
    calls: dict[str, str] = {}  # tool_use id -> tool name

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("url"), str):
                urls.add(normalize_url(node["url"]))
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    for turn in turns:
        for block in turn["content"]:
            kind = block.get("type")
            if kind in ("web_search_tool_result", "web_fetch_tool_result"):
                collect(block.get("content"))
            elif kind == "tool_use" and block["name"] != SUBMIT_TOOL["name"]:
                calls[block["id"]] = block["name"]
            elif kind == "tool_result" and block.get("tool_use_id") in calls and not block.get("is_error"):
                name = calls[block["tool_use_id"]]
                values = sources.tool_values.setdefault(name, set())
                values.update(float(n) for n in numbers(str(block.get("content", ""))))
    return sources


@dataclass
class Checked:
    """Facts that passed the source check, the ones that did not, and which kept facts are computed."""

    findings: ResearchFindings
    dropped: list[Fact]
    computed: set[str]  # claims citing a tool whose figures are not in that tool's output


def verify(findings: ResearchFindings, sources: Sources) -> Checked:
    """Keep facts whose source this agent actually retrieved (see the module docstring)."""
    dropped: list[Fact] = []
    computed: set[str] = set()

    def ok(fact: Fact) -> bool:
        src = fact.source.strip()
        if src.startswith("tool:"):
            values = sources.tool_values.get(src.removeprefix("tool:"))
            good = values is not None
            if values is not None and not all(figure_in(n, values) for n in numbers(fact.claim)):
                computed.add(fact.claim)
        else:
            good = normalize_url(src) in sources.urls
        if not good:
            dropped.append(fact)
        return good

    topics = [t.model_copy(update={"facts": [f for f in t.facts if ok(f)]}) for t in findings.topics]
    kept = findings.model_copy(update={"topics": [t for t in topics if t.facts]})
    return Checked(kept, dropped, computed)


# --- running an agent ----------------------------------------------------------------------------


async def run(r: Researcher, log_turn: agent.OnAppend, emit: Emit) -> ResearchResult:
    """Run one research agent to completion. Never raises (except on cancellation); failures are in `.error`."""
    submitted: list[ResearchFindings] = []

    allowed = {t["name"] for t in r.tools}

    def handle(name: str, raw: Any) -> tuple[str, bool]:
        if name != SUBMIT_TOOL["name"]:
            if name not in allowed:
                return f"Tool {name} is not available to this agent.", True
            return run_tool(name, raw)
        try:
            submitted.append(ResearchFindings.model_validate(raw))
        except ValidationError as exc:
            return json.dumps({"INVALID_INPUT": exc.errors(include_url=False)}, default=str), True
        return "Findings recorded. Your task is complete: reply with just 'Done.'", False

    result = ResearchResult(r)
    messages = [agent.user_message(r.task)]
    try:
        async with asyncio.timeout(settings.research_timeout_s):
            for attempt in range(2):
                async for e in agent.run(
                    messages,
                    log_turn,
                    system=RESEARCH_SYSTEM.format(title=r.title),
                    tools=[*r.tools, SUBMIT_TOOL],
                    model=settings.research_model,
                    effort=settings.research_effort,
                    agent=r.name,
                    tool_handler=handle,
                ):
                    if e["type"] == "error":
                        result.error = e["message"]
                    elif e["type"] in ("tool_start", "tool_input", "tool_result", "usage", "retry"):
                        await emit(e)
                if submitted or result.error or attempt:
                    break
                messages.append(agent.user_message(NUDGE))
    except TimeoutError:
        result.error = f"timed out after {settings.research_timeout_s}s"
    except Exception as exc:
        log.exception("research agent %s failed", r.name)
        result.error = str(exc)

    if submitted:
        checked = verify(submitted[-1], retrieved_sources(messages))
        result.findings, result.dropped, result.computed = checked.findings, checked.dropped, checked.computed
        if not result.fact_count:
            gaps = "; ".join(submitted[-1].gaps[:2])
            result.error = "no source-checked facts" + (f" ({gaps})" if gaps else "")
            result.findings = None
    elif not result.error:
        result.error = "no findings submitted"
    return result


def render_dossier(results: list[ResearchResult]) -> str:
    """The source-checked facts as Markdown, one section per agent, with gaps and failures stated."""
    parts = []
    for res in results:
        title = res.researcher.title.capitalize()
        if not res.findings:
            parts.append(f"# {title}\n\nThis research failed ({res.error}); treat the area as unknown.")
            continue
        lines = [f"# {title}"]
        for topic in res.findings.topics:
            lines.append(f"\n## {topic.heading}")
            lines += [
                f"- {f.claim} ({f.as_of}; {f.source}"
                + ("; figures computed by the agent from this tool's data" if f.claim in res.computed else "")
                + ")"
                for f in topic.facts
            ]
        if res.findings.gaps:
            lines.append("\n## Not found or unconfirmed")
            lines += [f"- {g}" for g in res.findings.gaps]
        if res.dropped:
            lines.append(f"\n_{len(res.dropped)} facts were discarded because their source was never retrieved._")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)
