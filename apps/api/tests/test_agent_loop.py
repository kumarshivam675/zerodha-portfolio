"""The agent tool loop, replayed against recorded API responses (tests/fixtures/agent_loop.json)."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2 as httpx  # the HTTP client the Anthropic SDK 1.x uses
import pytest
from anthropic.types.beta import BetaMessage

from app.services import agent

TURNS = json.loads((Path(__file__).parent / "fixtures" / "agent_loop.json").read_text())


class FakeStream:
    """Replays one recorded message as the SDK's stream events (the subset the loop reads)."""

    def __init__(self, message: BetaMessage):
        self.message = message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def __aiter__(self):
        for block in self.message.content:
            yield SimpleNamespace(type="content_block_start", content_block=block)
            if block.type == "text":
                yield SimpleNamespace(type="text", text=block.text)
            yield SimpleNamespace(type="content_block_stop", content_block=block)

    async def get_final_message(self):
        return self.message


class FakeClient:
    """Serves scripted responses in order: a fixture name, or an exception to raise."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

    def stream(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return FakeStream(BetaMessage.model_validate(TURNS[step]))


def api_error(status: int) -> anthropic.APIStatusError:
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return anthropic.APIStatusError(f"status {status}", response=response, body=None)


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "test")

    async def no_sleep(_):
        return None

    monkeypatch.setattr(agent.asyncio, "sleep", no_sleep)

    def make(script):
        client = FakeClient(script)
        monkeypatch.setattr(agent, "client", lambda: client)
        return client

    return make


def drive(tool_handler=None):
    history: list[dict] = [agent.user_message("What is my largest holding?")]
    appended: list[dict] = []

    async def on_append(turn):
        appended.append(turn)

    handler = tool_handler or (lambda name, raw: (json.dumps({"holdings": ["INFY"]}), False))

    async def collect():
        return [e async for e in agent.run(history, on_append, tool_handler=handler, agent="tester")]

    return asyncio.run(collect()), history, appended


def types(events):
    return [e["type"] for e in events]


def test_tool_call_then_answer(harness):
    client = harness(["tool_turn", "answer_turn"])
    calls = []
    events, history, appended = drive(lambda name, raw: (calls.append(name), ('{"ok": 1}', False))[1])

    assert types(events) == [
        "text", "tool_start", "tool_input", "usage", "tool_result", "text", "usage", "done",
    ]  # fmt: skip
    assert all(e["agent"] == "tester" for e in events)
    assert calls == ["get_portfolio"]
    # history is append-only: assistant tool call, user tool result, assistant answer
    assert [t["role"] for t in history] == ["user", "assistant", "user", "assistant"] and appended == history[1:]
    assert history[2]["content"][0] == {
        "type": "tool_result", "tool_use_id": "toolu_01", "content": '{"ok": 1}', "is_error": False,
    }  # fmt: skip
    assert len(client.requests) == 2 and len(client.requests[1]["messages"]) == 3

    usage = [e for e in events if e["type"] == "usage"]
    assert usage[0]["cache_creation_input_tokens"] == 9000 and usage[1]["cache_read_input_tokens"] == 9000
    # 1200*4 + 80*20 + 9000*5 per million, then 300*4 + 40*20 + 9000*0.2 per million
    assert usage[0]["cost_usd"] == pytest.approx(0.0514) and usage[1]["cost_usd"] == pytest.approx(0.0038)


def test_pause_turn_resumes_and_counts_searches(harness):
    harness(["pause_turn", "answer_turn"])
    events, history, _ = drive()
    assert types(events)[-1] == "done" and len(history) == 3  # no tool_result turn for server tools
    usage = [e for e in events if e["type"] == "usage"]
    assert usage[0]["web_search_requests"] == 1 and usage[0]["cost_usd"] == pytest.approx(0.0026 + 0.01)


def test_refusal_is_an_error(harness):
    harness(["refusal_turn"])
    events, _, _ = drive()
    assert events[-1] == {"type": "error", "message": "The model declined this request.", "agent": "tester"}


def test_token_limit_mid_tool_call_is_an_error(harness):
    harness(["truncated_tool_turn"])
    events, _, _ = drive()
    assert events[-1]["type"] == "error" and "token limit" in events[-1]["message"]


def test_transient_errors_are_retried(harness):
    client = harness([api_error(529), anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")),
                      "answer_turn"])  # fmt: skip
    events, history, _ = drive()
    assert types(events) == ["retry", "retry", "text", "usage", "done"]
    assert len(history) == 2 and len(client.requests) == 3  # failed attempts never reach history


def test_client_errors_are_not_retried(harness):
    client = harness([api_error(400)])
    events, _, _ = drive()
    assert types(events) == ["error"] and "400" in events[0]["message"] and len(client.requests) == 1


def test_retries_are_bounded(harness):
    harness([api_error(529)] * (agent.MAX_RETRIES + 1))
    events, _, _ = drive()
    assert types(events) == ["retry"] * agent.MAX_RETRIES + ["error"]


def tool_call(tid: str) -> dict:
    return {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": "x", "input": {}}]}


def text(role: str, value: str) -> dict:
    return {"role": role, "content": [{"type": "text", "text": value}]}


def test_repair_history_answers_dangling_tool_calls():
    history = [text("user", "q1"), tool_call("t1"), text("user", "q2"), text("assistant", "a"), tool_call("t2")]
    repaired = agent.repair_history(history)
    # t1 was followed by a plain user message: its result is added at the start of that turn
    assert [b["type"] for b in repaired[2]["content"]] == ["tool_result", "text"]
    assert repaired[2]["content"][0]["tool_use_id"] == "t1" and repaired[2]["content"][0]["is_error"]
    # t2 ended the history: a result turn is appended
    assert repaired[-1]["role"] == "user" and repaired[-1]["content"][0]["tool_use_id"] == "t2"
    assert history[2] == text("user", "q2") and len(history) == 5  # the input is not modified


def test_repair_history_leaves_valid_history_alone():
    answered = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}
    history = [text("user", "q"), tool_call("t1"), answered, text("assistant", "a")]
    assert agent.repair_history(history) == history
    assert agent.interrupted_tool_results(history) is None


def test_unknown_models_are_costed_at_the_highest_rate():
    from app.services import pricing

    usage = {"model": "claude-future-9", "input_tokens": 1_000_000, "output_tokens": 0,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
             "web_search_requests": 0, "web_fetch_requests": 0}  # fmt: skip
    assert pricing.cost_usd(usage) == max(p["input"] for p in pricing.PRICES.values())
    assert pricing.price_of("claude-sonnet-5-5-20261015") == pricing.PRICES["claude-sonnet-5-5"]
