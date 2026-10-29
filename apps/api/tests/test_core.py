"""Pure functions the app depends on: XIRR maths, Slack privacy masking, the SQL guard, origin checks."""

import asyncio
from datetime import date

import pytest

from app.security import SameOriginWrites
from app.services import agent_tools, slack
from app.services.trades import xirr

# --- XIRR ----------------------------------------------------------------------------------------


def test_xirr_one_year_ten_percent():
    assert xirr([(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), 1100.0)]) == pytest.approx(0.10, abs=1e-6)


def test_xirr_handles_losses_and_multiple_flows():
    rate = xirr([(date(2024, 1, 1), -1000.0), (date(2024, 7, 1), -500.0), (date(2025, 7, 1), 1200.0)])
    assert rate is not None and -0.25 < rate < 0
    # the rate it returns is a root of the NPV
    flows = [(date(2024, 1, 1), -1000.0), (date(2024, 7, 1), -500.0), (date(2025, 7, 1), 1200.0)]
    t0 = flows[0][0]
    assert sum(a / (1 + rate) ** ((d - t0).days / 365) for d, a in flows) == pytest.approx(0, abs=1e-3)


@pytest.mark.parametrize(
    "flows",
    [
        [],
        [(date(2025, 1, 1), -1000.0)],  # no inflow
        [(date(2025, 1, 1), 500.0), (date(2025, 6, 1), 600.0)],  # no outflow
        [(date(2025, 1, 1), -1000.0), (date(2025, 1, 1), 1100.0)],  # same day
    ],
)
def test_xirr_undefined_cases(flows):
    assert xirr(flows) is None


# --- Slack privacy -------------------------------------------------------------------------------


@pytest.fixture
def hide(monkeypatch):
    monkeypatch.setattr(slack.settings, "slack_hide_amounts", True)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Hold {{40 shares}} now", "Hold ••• shares now"),
        ("Worth {{₹1.05 L}}", "Worth ₹•••"),
        ("P&L {{+₹21,400}} so far", "P&L +₹••• so far"),
        ("{{₹84,000 invested}}", "₹••• invested"),
        ("{{−₹2.3 cr}}", "−₹•••"),
        ("Price ₹4,280 and target ₹5,000", "Price ₹4,280 and target ₹5,000"),  # market data stays
    ],
)
def test_slack_masks_only_tagged_figures(hide, text, expected):
    assert slack._privacy(text) == expected


def test_slack_unmasks_when_disabled(monkeypatch):
    monkeypatch.setattr(slack.settings, "slack_hide_amounts", False)
    assert slack._privacy("Hold {{40 shares}}") == "Hold 40 shares"


# --- SQL tool guard ------------------------------------------------------------------------------


@pytest.mark.parametrize("sql", ["SELECT 1; DROP TABLE holdings", "select 1;select 2", "SELECT 1; -- x"])
def test_sql_tool_allows_one_statement(sql):
    content, is_error = agent_tools.run_tool("query_database", {"sql": sql})
    assert is_error and "single statement" in content


def test_tool_input_is_validated():
    content, is_error = agent_tools.run_tool("get_technicals", {"exchange": "NSE"})
    assert is_error and "INVALID_INPUT" in content and "tradingsymbol" in content
    assert agent_tools.run_tool("no_such_tool", {}) == ("Unknown tool: no_such_tool", True)


# --- cross-site writes ---------------------------------------------------------------------------


def call(method: str, headers: dict[str, str]) -> int:
    sent: list[dict] = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    asyncio.run(SameOriginWrites(app, {"http://localhost:3300"})(scope, None, send))
    return sent[0]["status"]


@pytest.mark.parametrize(
    "method, headers, status",
    [
        ("POST", {"origin": "http://localhost:3300"}, 200),  # the app itself
        ("POST", {}, 200),  # curl, the Next.js server, the scheduler
        ("POST", {"origin": "https://evil.example"}, 403),
        ("POST", {"sec-fetch-site": "cross-site"}, 403),
        ("DELETE", {"origin": "http://localhost:3300.evil.example"}, 403),
        ("GET", {"origin": "https://evil.example"}, 200),  # reads are not state-changing
    ],
)
def test_cross_site_writes_are_refused(method, headers, status):
    assert call(method, headers) == status
