"""Slack notifications via an Incoming Webhook: finished instrument analyses and daily briefs."""

import logging
import re

import requests

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
        words = re.sub(r"^[+\-−]?\s*(?:₹|Rs\.?|INR)?\s*[\d,]+(?:\.\d+)?\s*(?:lakhs?|crores?|L|Cr|cr|k|K)?(?![A-Za-z])", "", figure).strip()
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
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "_Truncated; open the app for the full report._"}]})
    blocks.append(
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "AI-generated analysis, not investment advice."}]}
    )
    return {"text": title, "blocks": blocks}


def notify(title: str, markdown: str, subtitle: str | None = None, link: str | None = None) -> bool:
    """Post to the configured webhook. Never raises: a Slack outage shouldn't break the app."""
    if not configured():
        return False
    try:
        resp = requests.post(settings.slack_webhook_url, json=build_payload(title, markdown, subtitle, link), timeout=15)
        if resp.status_code != 200:
            log.warning("Slack webhook returned %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except requests.RequestException as exc:
        log.warning("Slack webhook failed: %s", exc)
        return False
