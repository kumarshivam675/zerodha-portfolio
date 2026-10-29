"""Slack notifications via an Incoming Webhook: finished instrument analyses and daily briefs."""

import logging
import re

import anthropic
import requests
from pydantic import BaseModel, Field

from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

SECTION_LIMIT = 2900  # Slack caps a section block's text at 3000 characters
MAX_SECTIONS = 45  # and a message at 50 blocks
PRIVATE = re.compile(r"\{\{([^{}]+)\}\}")  # the analyst tags the investor's own figures {{...}}


def configured() -> bool:
    return bool(settings.slack_webhook_url)


def _privacy(text: str) -> str:
    if not settings.slack_hide_amounts:
        return PRIVATE.sub(r"\1", text)

    def mask(m: re.Match) -> str:
        figure = m.group(1).strip()
        sign = figure[0] if figure[:1] in "+-−" else ""
        currency = "₹" if "₹" in figure else ""
        words = re.sub(
            r"^[+\-−]?\s*(?:₹|Rs\.?|INR)?\s*[\d,]+(?:\.\d+)?\s*(?:lakhs?|crores?|L|Cr|cr|k|K)?(?![A-Za-z])", "", figure
        ).strip()
        # Not "***": Slack would read the asterisks as bold markers.
        return f"{sign}{currency}•••{' ' + words if words else ''}"

    return PRIVATE.sub(mask, text)


def to_mrkdwn(markdown: str) -> str:
    """Convert the analyst's GitHub-flavoured Markdown to Slack mrkdwn."""
    out: list[str] = []
    table_header = True
    for line in markdown.splitlines():
        stripped = line.strip()
        # Tables: Slack has none, so render each row as a bullet; drop separator rows.
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            filled = [c for c in cells if c]
            if filled and all(re.fullmatch(r":?-{2,}:?", c) for c in filled):  # separator row
                continue
            cells = filled
            if not cells:  # an empty header row ("| | |")
                table_header = False
                continue
            if table_header:
                out.append("*" + " · ".join(cells) + "*" if len(cells) > 2 else "")
                table_header = False
                continue
            out.append(f"• *{cells[0]}*: " + " · ".join(cells[1:]) if len(cells) > 1 else f"• {cells[0]}")
            continue
        table_header = True
        heading = re.match(r"^#{1,6}\s+(.*)", stripped)
        if heading:
            out.append(f"\n*{heading.group(1).strip()}*")
            continue
        line = re.sub(r"^(\s*)[-*]\s+", r"\1• ", line)
        out.append(line)
    text = "\n".join(out)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<\2|\1>", text)  # links
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)  # bold
    text = re.sub(r"(?<![*\w])__(.+?)__(?!\w)", r"*\1*", text)
    text = re.sub(r"~~(.+?)~~", r"~\1~", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _chunks(text: str) -> list[str]:
    """Split on paragraph boundaries into pieces that fit a section block."""
    chunks, current = [], ""
    for para in text.split("\n\n"):
        while len(para) > SECTION_LIMIT:  # a single huge paragraph: hard split on lines
            cut = para.rfind("\n", 0, SECTION_LIMIT)
            cut = cut if cut > 0 else SECTION_LIMIT
            chunks.append(para[:cut])
            para = para[cut:].lstrip("\n")
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > SECTION_LIMIT:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def build_payload(title: str, markdown: str, subtitle: str | None = None, link: str | None = None) -> dict:
    body = to_mrkdwn(_privacy(markdown))
    chunks = _chunks(body)
    truncated = len(chunks) > MAX_SECTIONS
    chunks = chunks[:MAX_SECTIONS]
    context = " · ".join(filter(None, [subtitle, f"<{link}|Open in app>" if link else None]))
    blocks: list[dict] = [{"type": "header", "text": {"type": "plain_text", "text": title[:150], "emoji": True}}]
    if context:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": context}]})
    blocks += [{"type": "section", "text": {"type": "mrkdwn", "text": c}} for c in chunks if c.strip()]
    if truncated:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "_Truncated; open the app for the full report._"}],
            }
        )
    blocks.append(
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "AI-generated analysis, not investment advice."}]}
    )
    return {"text": title, "blocks": blocks}


def _post(payload: dict) -> bool:
    """Post to the configured webhook. Never raises: a Slack outage shouldn't break the app."""
    if not configured():
        return False
    try:
        resp = requests.post(settings.slack_webhook_url, json=payload, timeout=15)
        if resp.status_code != 200:
            log.warning("Slack webhook returned %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except requests.RequestException as exc:
        log.warning("Slack webhook failed: %s", exc)
        return False


def notify(title: str, markdown: str, subtitle: str | None = None, link: str | None = None) -> bool:
    """Send a report as-is (converted to Slack formatting)."""
    return _post(build_payload(title, markdown, subtitle, link))


# --- Digest: a crisp, structured Slack version of a long report ---------------

DIGEST_THRESHOLD = 1800  # characters; shorter answers are sent as they are


class Figure(BaseModel):
    label: str = Field(description="Short label, e.g. 'Last price', 'Your P&L', 'P/E', 'RSI(14)'")
    value: str = Field(description="Value with units, e.g. '₹2,635 (+3.1%)', '52.7x', '77 (overbought)'")


class DigestSection(BaseModel):
    title: str = Field(description="Section title, e.g. 'Last 3 months', 'News & macro'")
    bullets: list[str] = Field(description="2-3 bullets, each at most 20 words, leading with the key number or date")


class Digest(BaseModel):
    verdict: str = Field(
        description="The bottom line in under 12 words, e.g. 'Hold; trim ~25% before Q2 results'. "
        "Empty if the report has no recommendation."
    )
    confidence: str = Field(description="'low', 'medium' or 'high' as stated in the report, else ''")
    summary: str = Field(description="At most 2 sentences (40 words): what matters most and why")
    key_figures: list[Figure] = Field(description="4-6 headline numbers; each value at most 6 words")
    sections: list[DigestSection] = Field(description="3-4 sections covering the report's substance in report order")
    watch: list[str] = Field(description="2-3 things to watch or that would change the view, each at most 20 words")


DIGEST_PROMPT = """Turn this investment report into a Slack digest someone can read in under a minute.

Rules:
- Use only facts and numbers that are in the report; do not add anything.
- Crisp: detailed enough to act on, short enough to read in a minute. One idea per bullet, no sub-clauses.
- Keep the decisive numbers, dates and named catalysts; drop filler, background, and caveats about tooling or sources.
- Keep every {{...}} tag exactly as written (they mark the investor's private figures). If you restate such a figure,
  wrap it in {{...}} too.
- Plain text with **bold** for emphasis only; no headings, no links, no tables.

Report:
"""


def make_digest(report: str) -> Digest | None:
    if not settings.anthropic_api_key:
        return None
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.parse(
            model=settings.slack_digest_model,
            max_tokens=4000,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": DIGEST_PROMPT + report}],
            output_format=Digest,
        )
        if response.stop_reason == "refusal":
            return None
        return response.parsed_output
    except Exception:  # fall back to the full report rather than lose the message
        log.exception("Slack digest failed")
        return None


def _md(text: str) -> str:
    return to_mrkdwn(_privacy(text))


def digest_payload(title: str, d: Digest, subtitle: str | None = None, link: str | None = None) -> dict:
    links = " · ".join(filter(None, [subtitle, f"<{link}|Open full report>" if link else None]))
    blocks: list[dict] = [{"type": "header", "text": {"type": "plain_text", "text": title[:150], "emoji": True}}]
    if links:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": links}]})
    if d.verdict:
        conf = f"   _Confidence: {d.confidence}_" if d.confidence else ""
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f":dart: *{_md(d.verdict)}*{conf}"}})
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _md(d.summary)[:SECTION_LIMIT]}})
    if d.key_figures:
        fields = [{"type": "mrkdwn", "text": f"*{_md(f.label)}*\n{_md(f.value)}"[:2000]} for f in d.key_figures[:10]]
        blocks.append({"type": "section", "fields": fields})
    blocks.append({"type": "divider"})
    for sec in d.sections[:6]:
        text = f"*{_md(sec.title)}*\n" + "\n".join(f"• {_md(b)}" for b in sec.bullets[:5])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text[:SECTION_LIMIT]}})
    if d.watch:
        text = "*:eyes: What to watch*\n" + "\n".join(f"• {_md(w)}" for w in d.watch[:5])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text[:SECTION_LIMIT]}})
    blocks.append(
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "AI-generated analysis, not investment advice."}]}
    )
    return {"text": f"{title}: {d.verdict or d.summary[:120]}", "blocks": blocks}


def notify_report(title: str, report: str, subtitle: str | None = None, link: str | None = None) -> bool:
    """Send a long report as a structured digest (full report in the app); short ones as-is."""
    if not configured():
        return False
    if len(report) > DIGEST_THRESHOLD:
        d = make_digest(report)
        if d:
            return _post(digest_payload(title, d, subtitle, link))
    return notify(title, report, subtitle, link)
