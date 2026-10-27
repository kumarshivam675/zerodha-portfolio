import json
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..db import session_scope
from ..models import Conversation, ConversationMessage
from ..services import agent, runs

router = APIRouter(prefix="/api/agent")


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
        return {
            **_conv(c),
            "messages": agent.display_messages(stored),
            "pending_user": run.user_text if run else None,
        }


@router.get("/conversations/{conversation_id}/stream")
async def stream_conversation(conversation_id: uuid.UUID):
    """Re-attach to a conversation's live run: replays its events so far, then follows it."""
    run = runs.get(conversation_id)
    if not run:
        raise HTTPException(404, "No run in progress")
    return _sse(run)


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
    with session_scope() as db:
        c = db.get(Conversation, conversation_id)
        if not c:
            raise HTTPException(404, "Conversation not found")
        if c.title == "New conversation":
            c.title = body.content.strip().splitlines()[0][:80]
    return await _start_run(conversation_id, agent.user_message(body.content), body.content)


class AnalyzeIn(BaseModel):
    tradingsymbol: str = Field(min_length=1, max_length=64)
    exchange: str = Field("NSE", max_length=8)
    kind: str = Field("equity", pattern="^(equity|mf)$")
    name: str | None = None


@router.post("/analyze")
async def analyze_instrument(body: AnalyzeIn):
    """Start a new conversation with a deep-dive analysis of one holding, streamed as SSE."""
    label = body.name if body.kind == "mf" and body.name else body.tradingsymbol
    with session_scope() as db:
        c = Conversation(title=f"Analysis: {label}"[:200])
        db.add(c)
        db.flush()
        conversation_id = c.id
    turn = agent.user_message(
        f"Analyse {label}",
        instructions=agent.instrument_analysis_prompt(body.tradingsymbol, body.exchange, body.kind, body.name),
    )
    return await _start_run(conversation_id, turn, f"Analyse {label}", announce=True)


async def _start_run(
    conversation_id: uuid.UUID, user_turn: dict, user_text: str, announce: bool = False
) -> StreamingResponse:
    """Persist the user turn, start the agent in the background, and stream its events.

    The run keeps going if the client disconnects; it can re-attach via /stream.
    """
    if runs.get(conversation_id):
        raise HTTPException(409, "The analyst is still working on this conversation")
    # If an earlier run died mid-loop the history may end on a user turn; the API
    # merges consecutive user turns, so it is replayed as-is.
    with session_scope() as db:
        history = [
            {"role": m.role, "content": m.content}
            for m in db.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.seq)
            )
        ]
    history.append(user_turn)

    async def persist(turn: dict) -> int:
        with session_scope() as db:
            seq = db.scalar(
                select(func.coalesce(func.max(ConversationMessage.seq), -1)).where(
                    ConversationMessage.conversation_id == conversation_id
                )
            ) + 1
            db.add(ConversationMessage(conversation_id=conversation_id, seq=seq, role=turn["role"], content=turn["content"]))
            conv = db.get(Conversation, conversation_id)
            conv.updated_at = func.now()
        return seq

    start_seq = await persist(user_turn)
    run = runs.start(conversation_id, history, persist, start_seq, user_text)
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
