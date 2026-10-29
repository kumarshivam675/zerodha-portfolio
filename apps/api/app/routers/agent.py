import asyncio
import json
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..config import get_settings
from ..db import session_scope
from ..models import AgentRun, AgentStep, Conversation, ConversationMessage
from ..services import agent, analysis, runs, slack

router = APIRouter(prefix="/api/agent")
settings = get_settings()


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


def _conv(c: Conversation) -> dict:
    return {
        "id": str(c.id),
        "title": c.title,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
        "running": runs.get(c.id) is not None,
    }


@router.get("/conversations")
def list_conversations():
    with session_scope() as db:
        return [_conv(c) for c in db.scalars(select(Conversation).order_by(Conversation.updated_at.desc()))]


@router.post("/conversations")
def create_conversation():
    with session_scope() as db:
        c = Conversation()
        db.add(c)
        db.flush()
        db.refresh(c)
        return _conv(c)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: uuid.UUID):
    with session_scope() as db:
        c = db.get(Conversation, conversation_id)
        if not c:
            raise HTTPException(404, "Conversation not found")
        run = runs.get(c.id)
        # While a run is live, its turns are delivered by replaying the run's events instead.
        stored = [(m.role, m.content) for m in c.messages if not run or m.seq < run.start_seq]
        history = db.scalars(select(AgentRun).where(AgentRun.conversation_id == c.id).order_by(AgentRun.started_at))
        return {
            **_conv(c),
            "messages": agent.display_messages(stored),
            "pending_user": run.user_text if run else None,
            "runs": [
                {
                    "kind": r.kind,
                    "status": r.status,
                    "error": r.error,
                    "cost_usd": round(r.cost_usd, 4),
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "started_at": r.started_at.isoformat(),
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                }
                for r in history
            ],
        }


@router.get("/conversations/{conversation_id}/stream")
async def stream_conversation(conversation_id: uuid.UUID):
    """Re-attach to a conversation's live run: replays its events so far, then follows it."""
    run = runs.get(conversation_id)
    if not run:
        raise HTTPException(404, "No run in progress")
    return _sse(run)


@router.post("/conversations/{conversation_id}/cancel")
async def cancel_run(conversation_id: uuid.UUID):
    """Stop the conversation's live run, including every agent of an Analyse pipeline."""
    if not runs.cancel(conversation_id):
        raise HTTPException(404, "No run in progress")
    return {"cancelled": str(conversation_id)}


@router.get("/conversations/{conversation_id}/steps")
def get_steps(conversation_id: uuid.UUID):
    """The Analyse pipeline's trace: every research, strategy and risk agent turn and result."""
    with session_scope() as db:
        if not db.get(Conversation, conversation_id):
            raise HTTPException(404, "Conversation not found")
        steps = db.scalars(select(AgentStep).where(AgentStep.conversation_id == conversation_id).order_by(AgentStep.id))
        return [
            {"agent": s.agent, "kind": s.kind, "content": s.content, "created_at": s.created_at.isoformat()}
            for s in steps
        ]


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: uuid.UUID):
    with session_scope() as db:
        c = db.get(Conversation, conversation_id)
        if not c:
            raise HTTPException(404, "Conversation not found")
        db.delete(c)
    return {"deleted": str(conversation_id)}


@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: uuid.UUID, body: MessageIn):
    """Append a user message and stream the agent's reply as Server-Sent Events."""
    reservation = _reserve(conversation_id, "chat")

    def name_conversation() -> bool:
        with session_scope() as db:
            c = db.get(Conversation, conversation_id)
            if c and c.title == "New conversation":
                c.title = body.content.strip().splitlines()[0][:80]
            return c is not None

    try:
        if not await asyncio.to_thread(name_conversation):
            raise HTTPException(404, "Conversation not found")
        question = " ".join(body.content.split())
        title = f":speech_balloon: {question[:120]}{'…' if len(question) > 120 else ''}"
        notify = _slack_hook(conversation_id, title, "Analyst", settings.slack_notify_chat)
        return await _start_run(
            reservation, conversation_id, agent.user_message(body.content), body.content, on_complete=notify
        )
    except BaseException:
        runs.release(conversation_id, reservation)
        raise


class AnalyzeIn(BaseModel):
    tradingsymbol: str = Field(min_length=1, max_length=64)
    exchange: str = Field("NSE", max_length=8)
    kind: Literal["equity", "mf"] = "equity"
    name: str | None = None


@router.post("/analyze")
async def analyze_instrument(body: AnalyzeIn):
    """Start a new conversation with a deep-dive analysis of one holding, streamed as SSE.

    The analysis is a multi-agent pipeline (research -> strategy -> risk); follow-up questions in
    the same conversation go to the regular analyst, with the final report in its history.
    """
    conversation_id = uuid.uuid4()
    reservation = _reserve(conversation_id, "analysis", limit=settings.max_concurrent_analyses)
    label = body.name if body.kind == "mf" and body.name else body.tradingsymbol

    def create_conversation() -> None:
        with session_scope() as db:
            db.add(Conversation(id=conversation_id, title=f"Analysis: {label}"[:200]))

    try:
        await asyncio.to_thread(create_conversation)
        subject = analysis.Subject(body.tradingsymbol, body.exchange, body.kind, body.name)
        kind = "Fund" if body.kind == "mf" else body.exchange
        notify = _slack_hook(conversation_id, f":bar_chart: Analysis: {label}", kind, settings.slack_notify_analyses)
        return await _start_run(
            reservation,
            conversation_id,
            agent.user_message(f"Analyse {label}"),
            f"Analyse {label}",
            announce=True,
            on_complete=notify,
            source=lambda persist: analysis.analyze(subject, conversation_id, persist),
        )
    except BaseException:
        runs.release(conversation_id, reservation)
        raise


def _reserve(conversation_id: uuid.UUID, kind: str, limit: int | None = None) -> runs.Run:
    """Claim the conversation for a run before the handler's first await (see runs.reserve)."""
    try:
        return runs.reserve(conversation_id, kind, limit)
    except runs.Busy:
        raise HTTPException(409, "The analyst is still working on this conversation") from None
    except runs.AtCapacity:
        raise HTTPException(429, "Too many analyses running; wait for one to finish or cancel it.") from None


def _slack_hook(conversation_id: uuid.UUID, title: str, subtitle: str | None, enabled: bool):
    """Completion hook that posts the run's answer (or its failure) to Slack."""
    if not enabled or not slack.configured():
        return None

    def notify(run: runs.Run) -> None:
        link = f"{settings.web_url}/agent?c={conversation_id}"
        answer = run.final_text()
        if answer:
            slack.notify_report(title, answer, subtitle=subtitle, link=link)
        else:
            slack.notify(f"Failed: {title}", run.error() or "The run ended without an answer.", link=link)

    return notify


async def _start_run(
    reservation: runs.Run,
    conversation_id: uuid.UUID,
    user_turn: dict,
    user_text: str,
    announce: bool = False,
    on_complete=None,
    source=None,
) -> StreamingResponse:
    """Persist the user turn, start the reserved run in the background, and stream its events.

    The run keeps going if the client disconnects; it can re-attach via /stream. If an earlier run
    died mid-loop the history may end on a user turn; the API merges consecutive user turns, so it is
    replayed as-is. Tool calls an earlier run never answered are filled in (agent.repair_history).
    """

    def load_history() -> list[dict]:
        with session_scope() as db:
            return [
                {"role": m.role, "content": m.content}
                for m in db.scalars(
                    select(ConversationMessage)
                    .where(ConversationMessage.conversation_id == conversation_id)
                    .order_by(ConversationMessage.seq)
                )
            ]

    history = agent.repair_history(await asyncio.to_thread(load_history))
    history.append(user_turn)

    def save_turn(turn: dict) -> int:
        with session_scope() as db:
            last = db.scalar(
                select(func.max(ConversationMessage.seq)).where(ConversationMessage.conversation_id == conversation_id)
            )
            seq = 0 if last is None else last + 1
            db.add(
                ConversationMessage(
                    conversation_id=conversation_id, seq=seq, role=turn["role"], content=turn["content"]
                )
            )
            conv = db.get(Conversation, conversation_id)
            if conv:
                conv.updated_at = func.now()
        return seq

    async def persist(turn: dict) -> int:
        return await asyncio.to_thread(save_turn, turn)

    start_seq = await persist(user_turn)
    run = await runs.start(
        reservation,
        conversation_id,
        history,
        persist,
        start_seq,
        user_text,
        on_complete,
        source=source(persist) if source else None,
    )
    return _sse(run, announce=str(conversation_id) if announce else None)


def _sse(run: runs.Run, announce: str | None = None) -> StreamingResponse:
    async def events():
        if announce:
            yield f"data: {json.dumps({'type': 'conversation', 'id': announce})}\n\n"
        async for event in run.subscribe():
            if event is None:
                yield ": keep-alive\n\n"
            else:
                yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
