"""Agent runs that outlive the HTTP request that started them.

Each run drives the agent loop in a background task and buffers its events, so a client
can disconnect (close the panel, switch pages) and later re-attach to replay and follow it.
State is in-process: runs are lost if the API restarts, but every turn is persisted as it
completes, so the conversation history survives.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

from . import agent

log = logging.getLogger(__name__)
KEEPALIVE_SECONDS = 15


class Run:
    def __init__(self, start_seq: int, user_text: str):
        self.start_seq = start_seq  # seq of the user turn that started this run
        self.user_text = user_text
        self.events: list[dict] = []
        self.done = False
        self._cond = asyncio.Condition()

    async def publish(self, event: dict) -> None:
        async with self._cond:
            self.events.append(event)
            self._cond.notify_all()

    async def finish(self) -> None:
        async with self._cond:
            self.done = True
            self._cond.notify_all()

    async def subscribe(self) -> AsyncIterator[dict | None]:
        """Replay every event so far, then follow live ones. Yields None as a keep-alive tick."""
        i = 0
        while True:
            async with self._cond:
                try:
                    await asyncio.wait_for(
                        self._cond.wait_for(lambda: i < len(self.events) or self.done), KEEPALIVE_SECONDS
                    )
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


def start(
    conversation_id: uuid.UUID,
    history: list[dict],
    persist: Callable[[dict], Awaitable[None]],
    start_seq: int,
    user_text: str,
) -> Run:
    run = Run(start_seq, user_text)
    _runs[conversation_id] = run

    async def drive() -> None:
        try:
            async for event in agent.run(history, persist):
                await run.publish(event)
        except Exception as exc:  # surface unexpected failures to watchers
            log.exception("agent run failed")
            await run.publish({"type": "error", "message": f"Agent failed: {exc}"})
        finally:
            await run.finish()
            if _runs.get(conversation_id) is run:
                del _runs[conversation_id]

    task = asyncio.create_task(drive())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return run
