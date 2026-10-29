"""Agent runs that outlive the HTTP request that started them.

Each run drives an event source (the chat agent loop or the Analyse pipeline) in a background task and
buffers its events, so a client can disconnect (close the panel, switch pages) and later re-attach to
replay and follow it. Runs execute in the API process: the `agent_runs` table records each one (status,
cost, tokens), and on startup any run still marked running is closed as interrupted, with a note
appended to its conversation, so a restart never leaves a run silently hanging. Every turn is persisted
as it completes, so conversation history survives either way.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from ..db import session_scope
from ..models import AgentRun, Conversation, ConversationMessage
from . import agent
from .events import Event

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
KEEPALIVE_SECONDS = 15
INTERRUPTED_NOTE = "_This run was interrupted because the server restarted before it finished. Ask again to retry._"


class Run:
    def __init__(self, run_id: uuid.UUID, kind: str, start_seq: int, user_text: str):
        self.id = run_id
        self.kind = kind  # "chat" | "analysis"
        self.start_seq = start_seq  # seq of the user turn that started this run
        self.user_text = user_text
        self.events: list[Event] = []
        self.done = False
        self.status = "running"
        self.cost_usd = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.task: asyncio.Task | None = None
        self._cond = asyncio.Condition()

    async def publish(self, event: Event) -> None:
        if event["type"] == "usage":
            self.cost_usd += event["cost_usd"]
            self.input_tokens += event["input_tokens"] + event["cache_read_input_tokens"]
            self.output_tokens += event["output_tokens"]
        async with self._cond:
            self.events.append(event)
            self._cond.notify_all()

    async def finish(self) -> None:
        async with self._cond:
            self.done = True
            self._cond.notify_all()

    def final_text(self) -> str:
        """The answer: text streamed after the last tool call or retry (earlier text is progress
        narration, or partial output of a turn that was re-issued)."""
        last = max((i for i, e in enumerate(self.events) if e["type"] in ("tool_input", "retry")), default=-1)
        return "".join(e["text"] for e in self.events[last + 1 :] if e["type"] == "text").strip()

    def error(self) -> str | None:
        return next((e["message"] for e in self.events if e["type"] == "error"), None)

    async def subscribe(self) -> AsyncIterator[Event | None]:
        """Replay every event so far, then follow live ones. Yields None as a keep-alive tick."""
        i = 0
        while True:

            def ready(seen: int = i) -> bool:
                return seen < len(self.events) or self.done

            async with self._cond:
                try:
                    await asyncio.wait_for(self._cond.wait_for(ready), KEEPALIVE_SECONDS)
                except TimeoutError:
                    batch, finished = [], False
                else:
                    batch, finished = self.events[i:], self.done
                    i += len(batch)
            if not batch and not finished:
                yield None
                continue
            for event in batch:
                yield event
            if finished and i >= len(self.events):
                return


_runs: dict[uuid.UUID, Run] = {}
_tasks: set[asyncio.Task] = set()


def get(conversation_id: uuid.UUID) -> Run | None:
    run = _runs.get(conversation_id)
    return run if run and not run.done else None


def active(kind: str) -> int:
    return sum(1 for r in _runs.values() if r.kind == kind and not r.done)


class Busy(Exception):
    """The conversation already has a run in progress."""


class AtCapacity(Exception):
    """Too many runs of this kind are in progress."""


def reserve(conversation_id: uuid.UUID, kind: str, limit: int | None = None) -> Run:
    """Claim the conversation (and a slot of `kind`) for a new run. Synchronous on purpose: called
    before the request handler first awaits, it cannot interleave with another request's check."""
    if get(conversation_id):
        raise Busy
    if limit is not None and active(kind) >= limit:
        raise AtCapacity
    run = Run(uuid.uuid4(), kind, start_seq=-1, user_text="")
    _runs[conversation_id] = run
    return run


def release(conversation_id: uuid.UUID, run: Run) -> None:
    """Give back a reservation whose run never started (e.g. the request failed first)."""
    if run.task is None and _runs.get(conversation_id) is run:
        del _runs[conversation_id]


def cancel(conversation_id: uuid.UUID) -> bool:
    """Cancel a conversation's live run (and, for Analyse, every agent in it). False if none is running."""
    run = get(conversation_id)
    if not run or not run.task:
        return False
    run.task.cancel()
    return True


async def start(
    run: Run,
    conversation_id: uuid.UUID,
    history: list[dict],
    persist: Callable[[dict], Awaitable[object]],
    start_seq: int,
    user_text: str,
    on_complete: Callable[[Run], None] | None = None,
    source: AsyncGenerator[Event, None] | None = None,
) -> Run:
    """Start a reserved run in the background. `on_complete` runs (in a thread) once it ends, unless
    cancelled. `source` replaces the default chat agent loop with another event stream (e.g. the
    Analyse pipeline). If recording the run fails, the reservation is released and the error raised.
    """
    run.start_seq, run.user_text = start_seq, user_text
    try:
        await asyncio.to_thread(_record_start, run, conversation_id)
    except Exception:
        release(conversation_id, run)
        if source:
            await source.aclose()
        raise

    async def drive() -> None:
        events = source or agent.run(history, persist)
        try:
            async for event in events:
                await run.publish(event)
            run.status = "failed" if run.error() else "succeeded"
        except asyncio.CancelledError:
            run.status = "cancelled"
            await run.publish({"type": "error", "message": "Cancelled."})
        except Exception as exc:  # surface unexpected failures to watchers
            log.exception("agent run failed")
            run.status = "failed"
            await run.publish({"type": "error", "message": f"Agent failed: {exc}"})
        finally:
            await events.aclose()  # stop the source's own tasks (e.g. research agents) now, not at GC
            # A run stopped between a tool call and its result would leave history the API rejects
            # forever; record the unanswered calls as interrupted.
            if (results := agent.interrupted_tool_results(history)) is not None:
                history.append(results)
                try:
                    await persist(results)
                except Exception:
                    log.exception("could not record interrupted tool results")
            await run.finish()
            await asyncio.to_thread(_record_finish, run)
            if on_complete and run.status != "cancelled":
                try:
                    await asyncio.to_thread(on_complete, run)
                except Exception:
                    log.exception("run completion hook failed")
            if _runs.get(conversation_id) is run:
                del _runs[conversation_id]

    run.task = asyncio.create_task(drive())
    _tasks.add(run.task)
    run.task.add_done_callback(_tasks.discard)
    return run


def _record_start(run: Run, conversation_id: uuid.UUID) -> None:
    with session_scope() as db:
        db.add(AgentRun(id=run.id, conversation_id=conversation_id, kind=run.kind, status="running"))


def _record_finish(run: Run) -> None:
    try:
        with session_scope() as db:
            row = db.get(AgentRun, run.id)
            if row:  # the conversation (and its runs) may have been deleted meanwhile
                row.status, row.error = run.status, run.error()
                row.cost_usd, row.input_tokens, row.output_tokens = run.cost_usd, run.input_tokens, run.output_tokens
                row.finished_at = datetime.now(IST)
    except Exception:
        log.exception("could not record run %s", run.id)


def recover_interrupted() -> int:
    """Close runs a previous process left running; note it in their conversations. Call once at startup."""
    with session_scope() as db:
        stale = list(db.scalars(select(AgentRun).where(AgentRun.status == "running")))
        for row in stale:
            row.status, row.error, row.finished_at = "interrupted", "Server restarted", datetime.now(IST)
            last = db.scalar(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == row.conversation_id)
                .order_by(ConversationMessage.seq.desc())
                .limit(1)
            )
            if last is None:
                continue
            seq = last.seq
            results = agent.interrupted_tool_results([{"role": last.role, "content": last.content}])
            if results is not None:  # stopped mid tool call: answer the calls so history stays valid
                seq += 1
                db.add(ConversationMessage(conversation_id=row.conversation_id, seq=seq, **results))
            if last.role == "user" or results is not None:  # keep history alternating; say what happened
                db.add(
                    ConversationMessage(
                        conversation_id=row.conversation_id,
                        seq=seq + 1,
                        role="assistant",
                        content=[{"type": "text", "text": INTERRUPTED_NOTE}],
                    )
                )
                conv = db.get(Conversation, row.conversation_id)
                if conv:
                    conv.updated_at = func.now()
    if stale:
        log.warning("marked %d interrupted run(s)", len(stale))
    return len(stale)
