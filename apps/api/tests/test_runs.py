"""Run management: replay, completion status, and cancellation that reaches the event source."""

import asyncio
import uuid

import pytest

from app.services import runs


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    monkeypatch.setattr(runs, "_record_start", lambda run, conversation_id: None)
    monkeypatch.setattr(runs, "_record_finish", lambda run: None)


async def noop(turn):
    return None


def test_completed_run_replays_every_event():
    async def source():
        yield {"type": "usage", "cost_usd": 0.25, "input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 3}
        yield {"type": "text", "text": "hello"}
        yield {"type": "done", "stop_reason": "end_turn"}

    async def scenario():
        cid = uuid.uuid4()
        run = await runs.start(runs.reserve(cid, "chat"), cid, [], noop, 0, "q", source=source())
        await run.task
        return run, [e async for e in run.subscribe()]

    run, events = asyncio.run(scenario())
    assert [e["type"] for e in events] == ["usage", "text", "done"]
    assert (run.status, run.cost_usd, run.input_tokens, run.output_tokens) == ("succeeded", 0.25, 15, 3)
    assert run.final_text() == "hello"


def test_error_event_marks_run_failed():
    async def source():
        yield {"type": "error", "message": "nope"}

    async def scenario():
        cid = uuid.uuid4()
        run = await runs.start(runs.reserve(cid, "chat"), cid, [], noop, 0, "q", source=source())
        await run.task
        return run

    run = asyncio.run(scenario())
    assert run.status == "failed" and run.error() == "nope"


def test_cancel_stops_the_source_and_skips_completion_hook():
    state = {"closed": False, "hook": False}

    async def source():
        try:
            yield {"type": "stage", "label": "working"}
            await asyncio.sleep(3600)
            yield {"type": "text", "text": "never"}
        finally:
            state["closed"] = True  # e.g. the pipeline cancelling its research agents

    def hook(run):
        state["hook"] = True

    async def scenario():
        cid = uuid.uuid4()
        run = await runs.start(runs.reserve(cid, "chat"), cid, [], noop, 0, "q", on_complete=hook, source=source())
        await asyncio.sleep(0.05)
        assert runs.get(cid) is run and runs.active("chat") == 1
        assert runs.cancel(cid)
        await asyncio.gather(run.task, return_exceptions=True)
        return cid, run

    cid, run = asyncio.run(scenario())
    assert state == {"closed": True, "hook": False}
    assert run.status == "cancelled" and run.error() == "Cancelled."
    assert runs.get(cid) is None and not runs.cancel(cid)


def test_reservation_is_exclusive_and_capped():
    async def scenario():
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ra = runs.reserve(a, "analysis", limit=2)
        with pytest.raises(runs.Busy):
            runs.reserve(a, "chat")
        rb = runs.reserve(b, "analysis", limit=2)
        with pytest.raises(runs.AtCapacity):
            runs.reserve(c, "analysis", limit=2)
        runs.release(b, rb)  # a request that failed before starting gives its slot back
        rc = runs.reserve(c, "analysis", limit=2)
        for cid, r in [(a, ra), (c, rc)]:
            runs.release(cid, r)
        return runs.active("analysis")

    assert asyncio.run(scenario()) == 0


def test_failed_run_record_releases_the_reservation(monkeypatch):
    def broken(run, conversation_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(runs, "_record_start", broken)

    async def source():
        yield {"type": "text", "text": "never"}

    async def scenario():
        cid = uuid.uuid4()
        with pytest.raises(RuntimeError):
            await runs.start(runs.reserve(cid, "chat"), cid, [], noop, 0, "q", source=source())
        return runs.get(cid)

    assert asyncio.run(scenario()) is None


def test_final_text_ignores_output_of_retried_turns():
    run = runs.Run(uuid.uuid4(), "chat", 0, "q")
    run.events = [{"type": "text", "text": "partial ans"}, {"type": "retry", "reason": "529"},
                  {"type": "text", "text": "full answer"}]  # fmt: skip
    assert run.final_text() == "full answer"


def test_run_stopped_mid_tool_call_records_interrupted_results():
    persisted: list[dict] = []
    history = [{"role": "user", "content": [{"type": "text", "text": "q"}]}]

    async def persist(turn):
        persisted.append(turn)

    async def source():
        # what agent.run does: append the assistant tool call, then (here) get cancelled before the result
        history.append({"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}]})
        yield {"type": "tool_start", "id": "t1", "name": "x"}
        await asyncio.sleep(3600)

    async def scenario():
        cid = uuid.uuid4()
        run = await runs.start(runs.reserve(cid, "chat"), cid, history, persist, 0, "q", source=source())
        await asyncio.sleep(0.05)
        runs.cancel(cid)
        await asyncio.gather(run.task, return_exceptions=True)

    asyncio.run(scenario())
    assert persisted == [history[-1]] and history[-1]["role"] == "user"
    assert history[-1]["content"][0]["tool_use_id"] == "t1" and history[-1]["content"][0]["is_error"]
