"""Chat API router — main endpoint for all channels."""

import uuid
import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.services.agent_service import AgentService

logger = structlog.get_logger()
router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    channel: str = "web"  # web, telegram, whatsapp


class ChatResponse(BaseModel):
    response: str
    session_id: str
    language: str
    intent: str
    needs_escalation: bool
    sources: list[str] = []


@router.post("/", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest):
    """Process a chat message through the AI agent pipeline."""

    # Rate limiting
    redis = request.app.state.redis
    session_id = body.session_id or str(uuid.uuid4())
    rate = await redis.increment_rate(session_id)
    if rate > 30:  # max 30 msgs per minute per session
        return ChatResponse(
            response="U stuurt te veel berichten. Wacht even en probeer het opnieuw.",
            session_id=session_id,
            language="nl",
            intent="rate_limited",
            needs_escalation=False,
        )

    # Process through agent
    agent = AgentService(
        qdrant=request.app.state.qdrant,
        redis=redis,
    )

    result = await agent.process_message(
        message=body.message,
        session_id=session_id,
        channel=body.channel,
    )

    return ChatResponse(**result)


@router.delete("/{session_id}")
async def clear_session(request: Request, session_id: str):
    """Clear a chat session."""
    await request.app.state.redis.clear_history(session_id)
    return {"status": "ok", "session_id": session_id}
