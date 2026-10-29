"""The strategy agent: no tools, writes the report and a structured verdict from the dossier and position."""

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any

import anthropic

from ...config import get_settings
from .. import agent
from ..events import Event
from .prompts import STRATEGY_SYSTEM
from .schemas import StrategyOutput

settings = get_settings()


class StrategyError(RuntimeError):
    pass


class Strategy:
    """One strategy conversation; `revise` continues it, so each revision sees its earlier draft."""

    def __init__(self, brief: str, log_turn: agent.OnAppend):
        self.messages: list[dict] = [agent.user_message(brief)]
        self.log_turn = log_turn
        self.output: StrategyOutput | None = None

    async def write(self) -> AsyncGenerator[Event, None]:
        """Stream reasoning summaries and usage; the result lands in `self.output`."""
        for attempt in range(agent.MAX_RETRIES + 1):
            started = time.monotonic()
            try:
                async with asyncio.timeout(settings.strategy_timeout_s):
                    params: dict[str, Any] = {
                        "model": settings.strategy_model,
                        "max_tokens": 64000,
                        "system": STRATEGY_SYSTEM,
                        "messages": self.messages,
                        "thinking": {"type": "adaptive", "display": "summarized"},
                        "output_config": {"effort": settings.agent_effort},
                    }
                    async with agent.client().messages.stream(**params, output_format=StrategyOutput) as stream:
                        async for event in stream:
                            if event.type == "thinking":
                                yield {"type": "thinking", "text": event.thinking, "agent": "strategy"}
                        msg = await stream.get_final_message()
                break
            except TimeoutError as exc:
                raise StrategyError(f"timed out after {settings.strategy_timeout_s}s") from exc
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
                if not agent.is_transient(exc) or attempt == agent.MAX_RETRIES:
                    raise StrategyError(agent.describe_error(exc)) from exc
                yield {"type": "retry", "reason": agent.describe_error(exc), "agent": "strategy"}
                await asyncio.sleep(2 ** (attempt + 1))
        yield {**agent.usage_event(msg, started), "agent": "strategy"}

        turn = {"role": "assistant", "content": agent.dump_blocks(msg.content)}
        self.messages.append(turn)
        await self.log_turn(turn)
        if msg.stop_reason == "refusal":
            raise StrategyError("The model declined to write the report.")
        if msg.parsed_output is None:
            raise StrategyError(f"No structured report (stop reason: {msg.stop_reason}).")
        self.output = msg.parsed_output

    def revise(self, feedback: str) -> None:
        self.messages.append(agent.user_message(feedback))
        self.output = None
