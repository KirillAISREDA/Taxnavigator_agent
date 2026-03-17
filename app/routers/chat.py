"""Chat API router — text chat + file upload endpoints."""

import uuid
import structlog
from fastapi import APIRouter, Request, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional

from app.services.agent_service import AgentService
from app.services.document_service import DocumentService

logger = structlog.get_logger()
router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    channel: str = "web"


class ChatResponse(BaseModel):
    response: str
    session_id: str
    language: str
    intent: str
    needs_escalation: bool
    sources: list[str] = []
    document_type: str | None = None


# ──────────────────────────────────────────────────────────────────
# POST /api/chat/ — text only (backward-compatible)
# ──────────────────────────────────────────────────────────────────
@router.post("/", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest):
    redis = request.app.state.redis
    session_id = body.session_id or str(uuid.uuid4())

    rate = await redis.increment_rate(session_id)
    if rate > 30:
        return ChatResponse(
            response="U stuurt te veel berichten. Wacht even en probeer het opnieuw.",
            session_id=session_id, language="nl",
            intent="rate_limited", needs_escalation=False,
        )

    agent = AgentService(qdrant=request.app.state.qdrant, redis=redis)
    result = await agent.process_message(
        message=body.message, session_id=session_id, channel=body.channel,
    )
    return ChatResponse(**result)


# ──────────────────────────────────────────────────────────────────
# POST /api/chat/upload — file + optional text message
# ──────────────────────────────────────────────────────────────────
@router.post("/upload", response_model=ChatResponse)
async def chat_with_file(
    request: Request,
    file: UploadFile = File(...),
    message: str = Form(default=""),
    session_id: Optional[str] = Form(default=None),
    channel: str = Form(default="web"),
):
    """Analyze an uploaded document/image and respond in context."""
    redis = request.app.state.redis
    session_id = session_id or str(uuid.uuid4())

    rate = await redis.increment_rate(session_id)
    if rate > 30:
        return ChatResponse(
            response="U stuurt te veel berichten. Wacht even en probeer het opnieuw.",
            session_id=session_id, language="nl",
            intent="rate_limited", needs_escalation=False,
        )

    # ── read & validate ───────────────────────────────────────────
    doc_svc = DocumentService()
    file_data = await file.read()
    fname = file.filename or "document"

    ok, err = doc_svc.validate_file(fname, len(file_data))
    if not ok:
        return ChatResponse(
            response=f"❌ {err}", session_id=session_id,
            language="en", intent="file_error", needs_escalation=False,
        )

    # ── detect language from SESSION, not just the message field ──
    agent = AgentService(qdrant=request.app.state.qdrant, redis=redis)
    language = await agent.detect_language_with_session(message, session_id)

    # ── GPT-4o Vision analysis ────────────────────────────────────
    try:
        doc = await doc_svc.analyze_document(
            file_data=file_data, filename=fname,
            user_message=message, language=language,
        )
    except Exception:
        return ChatResponse(
            response="Er is een fout opgetreden bij het analyseren. Probeer het opnieuw.",
            session_id=session_id, language="nl",
            intent="file_error", needs_escalation=False,
        )

    # ── optional RAG enrichment ───────────────────────────────────
    doc_type = doc["document_type"]
    cat_map = {
        "tax_assessment": ["tax"], "tax_letter": ["tax"],
        "kvk_extract": ["business_registration"],
        "residence_permit": ["ukrainian_status"],
        "toeslagen": ["tax", "ukrainian_support"],
    }
    sources: list[str] = []
    categories = cat_map.get(doc_type, [])
    if categories:
        chunks = await agent.qdrant.search(
            query=message or doc_type, categories=categories, limit=3,
        )
        sources = [c["source_url"] for c in chunks if c.get("source_url")]

    # ── save to session history ───────────────────────────────────
    user_note = f"[📄 {fname}]"
    if message:
        user_note += f"\n{message}"
    await redis.add_to_history(session_id, "user", user_note)
    await redis.add_to_history(session_id, "assistant", doc["analysis"])

    return ChatResponse(
        response=doc["analysis"], session_id=session_id,
        language=language, intent=f"document_{doc_type}",
        needs_escalation=doc["needs_escalation"],
        sources=sources, document_type=doc_type,
    )


# ──────────────────────────────────────────────────────────────────
@router.delete("/{session_id}")
async def clear_session(request: Request, session_id: str):
    await request.app.state.redis.clear_history(session_id)
    return {"status": "ok", "session_id": session_id}
