"""Portfolio agent: Claude with portfolio tools plus web search/fetch, streamed as events."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

from ..config import get_settings
from .agent_tools import TOOLS, run_tool

settings = get_settings()
IST = ZoneInfo("Asia/Kolkata")
MAX_TURNS = 30

SYSTEM_PROMPT = """\
You are a personal investment analyst working for one investor who holds Indian equities and mutual \
funds through Zerodha. You have read-only access to their portfolio database (current holdings, saved \
daily snapshots, daily price history) and can search and read the web.

How to work:
- Ground every claim about the portfolio in tool results; fetch data rather than assuming it.
- For news, results, corporate actions, sector trends or macro context, search the web and cite sources.
- Show the numbers behind a conclusion (weights, P&L, returns, valuation multiples) and state the date \
of the data you used. Prices are end-of-day unless the data says otherwise.
- When asked for decisions (buy/sell/hold/rebalance), give a clear recommendation with reasoning, the \
key risks, and what would change your view. Distinguish facts from judgement and say how confident you are.
- You cannot place orders. If an action makes sense, describe it so the investor can act in Kite.
- Amounts are in INR; format large numbers in lakh/crore where natural.

Privacy markup (required): the app can hide the investor's own figures. Wrap every figure that reveals \
the size of their holdings in double braces: quantities held or to trade, position and portfolio values, \
invested amounts, rupee P&L or gains, and proceeds of a suggested trade. Examples: {{40 shares}}, \
{{₹1.05 L}}, {{+₹21,400}}, {{₹84,000 invested}}. Do not wrap market data (share prices, targets, market cap, \
company financials, index levels), percentages, weights, or per-share prices including the average cost.
"""

Emit = dict
OnAppend = Callable[[dict], Awaitable[object]]


def _client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def user_message(content: str, instructions: str | None = None) -> dict:
    """A user turn. `instructions` are sent to the model but hidden from the chat transcript."""
    now = datetime.now(IST).strftime("%A %d %B %Y, %H:%M IST")
    blocks = [{"type": "text", "text": f"<context>Current time: {now}</context>"}]
    if instructions:
        blocks.append({"type": "text", "text": f"<instructions>\n{instructions}\n</instructions>"})
    blocks.append({"type": "text", "text": content})
    return {"role": "user", "content": blocks}


def _dump(blocks) -> list[dict]:
    return [b.model_dump(mode="json", exclude_none=True) for b in blocks]


async def run(messages: list[dict], on_append: OnAppend) -> AsyncIterator[Emit]:
    """Drive the tool loop over `messages` (which must end with a user turn).

    Every turn appended to the history is passed to `on_append` so callers can persist it;
    history is append-only so thinking blocks stay valid on replay.
    """
    if not settings.agent_configured:
        yield {"type": "error", "message": "ANTHROPIC_API_KEY is not set"}
        return

    client = _client()
    json_retries = 0
    for _ in range(MAX_TURNS):
        try:
            async with client.beta.messages.stream(
                model=settings.agent_model,
                max_tokens=64000,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": settings.agent_effort},
                betas=["server-side-fallback-2026-07-01"],
                extra_body={"fallbacks": "default", "cache_control": {"type": "ephemeral"}},
            ) as stream:
                async for event in stream:
                    if event.type == "text":
                        yield {"type": "text", "text": event.text}
                    elif event.type == "thinking":
                        yield {"type": "thinking", "text": event.thinking}
                    elif event.type == "content_block_start":
                        block = event.content_block
                        if block.type in ("tool_use", "server_tool_use"):
                            yield {"type": "tool_start", "id": block.id, "name": block.name}
                    elif event.type == "content_block_stop":
                        # Report each tool call as soon as its input is complete, in stream order.
                        block = event.content_block
                        if block.type in ("tool_use", "server_tool_use"):
                            yield {"type": "tool_input", "id": block.id, "name": block.name, "input": block.input}
                response = await stream.get_final_message()
            json_retries = 0
        except ValueError:
            # Tool input JSON the SDK could not parse; re-issue the turn (bounded).
            json_retries += 1
            if json_retries > 2:
                yield {"type": "error", "message": "Model produced malformed tool input repeatedly"}
                return
            continue
        except anthropic.APIStatusError as exc:
            yield {"type": "error", "message": f"Claude API error {exc.status_code}: {exc.message}"}
            return
        except anthropic.APIConnectionError:
            yield {"type": "error", "message": "Could not reach the Claude API"}
            return

        assistant = {"role": "assistant", "content": _dump(response.content)}
        messages.append(assistant)
        await on_append(assistant)

        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason == "refusal":
            yield {"type": "error", "message": "The model declined this request."}
            return

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            yield {"type": "done", "stop_reason": response.stop_reason}
            return
        if response.stop_reason == "max_tokens":
            yield {"type": "error", "message": "Response hit the token limit mid tool call."}
            return

        results = []
        for block in tool_uses:
            content, is_error = await asyncio.to_thread(run_tool, block.name, block.input)
            yield {"type": "tool_result", "id": block.id, "name": block.name, "is_error": is_error}
            results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}
            )
        tool_turn = {"role": "user", "content": results}
        messages.append(tool_turn)
        await on_append(tool_turn)

    yield {"type": "error", "message": f"Stopped after {MAX_TURNS} model turns"}


BRIEF_PROMPT = """\
Write today's portfolio brief. Review the current holdings and how they moved today and over recent \
snapshots, check the web for material news on the largest positions and the biggest movers, and end \
with a short, prioritised list of things I should consider doing (or explicitly: nothing to do). \
Keep it under 500 words, in Markdown."""


async def write_brief() -> str:
    messages = [user_message(BRIEF_PROMPT)]
    parts: list[str] = []

    async def noop(_: dict) -> None:
        return None

    async for event in run(messages, noop):
        if event["type"] == "error":
            raise RuntimeError(event["message"])
    # The final assistant turn holds the brief.
    for block in messages[-1]["content"]:
        if block.get("type") == "text":
            parts.append(block["text"])
    return "".join(parts).strip()


def display_messages(stored: list[tuple[str, list[dict]]]) -> list[dict]:
    """Collapse stored API turns into chat bubbles.

    Reasoning summaries and text written before a tool call become progress notes ("notes");
    only text after the last tool call is the answer ("text").
    """
    out: list[dict] = []
    for role, content in stored:
        if role == "user":
            texts = [
                b["text"]
                for b in content
                if b.get("type") == "text" and not b["text"].startswith(("<context>", "<instructions>"))
            ]
            if texts:
                out.append({"role": "user", "text": "\n".join(texts), "tools": [], "notes": []})
            continue
        if not out or out[-1]["role"] != "assistant":
            out.append({"role": "assistant", "text": "", "tools": [], "notes": []})
        bubble = out[-1]
        for b in content:
            kind = b.get("type")
            if kind == "thinking" and b.get("thinking", "").strip():
                bubble["notes"].append(b["thinking"].strip())
            elif kind == "text":
                bubble["text"] += b["text"]
            elif kind in ("tool_use", "server_tool_use"):
                if bubble["text"].strip():
                    bubble["notes"].append(bubble["text"].strip())
                bubble["text"] = ""
                bubble["tools"].append({"name": b["name"], "input": json.dumps(b.get("input", {}))})
    return out


def instrument_analysis_prompt(tradingsymbol: str, exchange: str, kind: str, name: str | None) -> str:
    """Hidden instructions for the per-instrument deep dive opened from the holdings table."""
    if kind == "mf":
        subject = f"the mutual fund {name or tradingsymbol} (ISIN {tradingsymbol})"
        data_steps = (
            "- Call get_portfolio for my position (units, average NAV, P&L, weight).\n"
            "- Search the web for the fund's factsheet: category, benchmark, AUM, expense ratio, top holdings, "
            "sector mix, fund manager, and 3M/6M/1Y returns versus its benchmark and category."
        )
    else:
        subject = f"{tradingsymbol} ({exchange})"
        data_steps = (
            f"- Call get_portfolio for my position (quantity, average cost, P&L, weight).\n"
            f"- Call get_technicals and get_stock_info for {tradingsymbol} on {exchange}.\n"
            "- Search the web for the last 6 months: quarterly results and guidance, management commentary, "
            "orders/contracts, corporate actions, broker rating changes and target prices, promoter/FII/DII "
            "holding changes, and regulatory developments."
        )
    return f"""Produce a deep-dive analysis of {subject} that I hold.

Gather data first:
{data_steps}
- Search for market-wide and geopolitical factors that could move it: sector news, commodity and input \
costs, currency, interest rates, RBI/Fed policy, government policy and budget items, trade/tariff and \
geopolitical developments, and global peers.

Then write the report in Markdown with these sections:
## Snapshot
A short table: last price, my average cost, my P&L, portfolio weight, market cap, P/E vs sector, 52-week range.
## Last 3 months
What happened (price move with % figures, key events, results, news) and why the price moved.
## Last 6 months
The broader trend, how the story has changed, and performance versus the Nifty 50 or the relevant benchmark.
## Technical picture
Trend versus 20/50/200-day averages, RSI, distance from 52-week high/low, volatility, support and \
resistance levels, volume trend.
## News and macro drivers
Recent company news, then geopolitical, policy, and macro factors, each with likely direction of impact \
(positive/negative) and how material it is.
## Risks and catalysts
Upcoming events (results dates, policy decisions) and the main downside risks.
## Verdict
Hold / add / trim / exit for my position specifically (consider my cost and weight), with confidence \
(low/medium/high) and what would change the view.

Cite sources inline as links. Give dates for news. Be concise and specific; no generic filler."""
