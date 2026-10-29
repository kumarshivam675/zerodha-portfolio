"""Estimated Claude API cost of a response, from its `usage` block.

List prices in USD per million tokens (cache writes at the 5-minute rate). Update when prices change;
unknown models are costed at the highest known rate. An estimate for budgets and dashboards, not billing.
"""

from typing import TypedDict

PRICES: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
}
WEB_SEARCH_USD = 10.00 / 1000  # per search; web fetch is billed as tokens only


class Usage(TypedDict):
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    web_search_requests: int
    web_fetch_requests: int


def usage_of(response) -> Usage:
    """Normalise an SDK response's usage (fields may be missing or None)."""
    u = response.usage
    server = getattr(u, "server_tool_use", None)
    return {
        "model": response.model,
        "input_tokens": u.input_tokens or 0,
        "output_tokens": u.output_tokens or 0,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", None) or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", None) or 0,
        "web_search_requests": getattr(server, "web_search_requests", None) or 0,
        "web_fetch_requests": getattr(server, "web_fetch_requests", None) or 0,
    }


def price_of(model: str) -> dict[str, float]:
    """List price for a model ID. Dated or suffixed IDs match their base model; an unknown model is
    priced at the most expensive known rate, so cost budgets never treat it as free."""
    for known, price in PRICES.items():
        if model == known or model.startswith(f"{known}-"):
            return price
    return {k: max(p[k] for p in PRICES.values()) for k in ("input", "output", "cache_read", "cache_write")}


def cost_usd(usage: Usage) -> float:
    price = price_of(usage["model"])
    tokens = (
        usage["input_tokens"] * price["input"]
        + usage["output_tokens"] * price["output"]
        + usage["cache_read_input_tokens"] * price["cache_read"]
        + usage["cache_creation_input_tokens"] * price["cache_write"]
    )
    return tokens / 1_000_000 + usage["web_search_requests"] * WEB_SEARCH_USD
