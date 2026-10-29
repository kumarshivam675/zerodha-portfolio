"""The event stream agent runs emit (sent to the browser as Server-Sent Events).

Mirrored by the `AgentEvent` type in apps/web/lib/useAgent.ts; keep the two in step.
`agent` names the pipeline agent that produced an event (Analyse runs several); chat leaves it out.
"""

from typing import Any, Literal, TypedDict


class _Event(TypedDict, total=False):
    agent: str


class ConversationEvent(_Event):
    type: Literal["conversation"]
    id: str


class TextEvent(_Event):
    type: Literal["text"]
    text: str


class ThinkingEvent(_Event):
    type: Literal["thinking"]
    text: str


class ToolStartEvent(_Event):
    type: Literal["tool_start"]
    id: str
    name: str


class ToolInputEvent(_Event):
    type: Literal["tool_input"]
    id: str
    name: str
    input: Any


class ToolResultEvent(_Event):
    type: Literal["tool_result"]
    id: str
    name: str
    is_error: bool


class UsageEvent(_Event):
    """One API response's token use and estimated cost."""

    type: Literal["usage"]
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    web_search_requests: int
    web_fetch_requests: int
    cost_usd: float
    duration_ms: int


class RetryEvent(_Event):
    """The current model turn failed transiently and is being re-issued; discard its partial output."""

    type: Literal["retry"]
    reason: str


class StageEvent(_Event):
    type: Literal["stage"]
    label: str


class NoteEvent(_Event):
    type: Literal["note"]
    text: str


class DoneEvent(_Event):
    type: Literal["done"]
    stop_reason: str


class ErrorEvent(_Event):
    type: Literal["error"]
    message: str


Event = (
    ConversationEvent
    | TextEvent
    | ThinkingEvent
    | ToolStartEvent
    | ToolInputEvent
    | ToolResultEvent
    | UsageEvent
    | RetryEvent
    | StageEvent
    | NoteEvent
    | DoneEvent
    | ErrorEvent
)
