"""Chat API router — text chat + file upload endpoints.

Supports mode parameter: "limited" (default) or "full".
"""

import uuid
import structlog
from fastapi import APIRouter, Request, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional

from app.services.agent_service import AgentService, MODE_LIMITED, MODE_FULL
from app.services.document_service import DocumentService
from app.services.transcription_service import transcribe_audio

logger = structlog.get_logger()
router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    channel: str = "web"
    mode: str = MODE_LIMITED  # "limited" or "full"


class ChatResponse(BaseModel):
    response: str
    session_id: str
    language: str
    intent: str
    needs_escalation: bool
    sources: list[str] = []
    document_type: str | None = None
    has_actions: bool = False


class TranslateRequest(BaseModel):
    text: str
    target_language: str


class TranslateResponse(BaseModel):
    translated: str
    target_language: str


# ──────────────────────────────────────────────────────────────────
# POST /api/chat/ — text only
# ──────────────────────────────────────────────────────────────────
@router.post("/", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest):
    redis = request.app.state.redis
    session_id = body.session_id or str(uuid.uuid4())
    mode = body.mode if body.mode in (MODE_LIMITED, MODE_FULL) else MODE_LIMITED

    rate = await redis.increment_rate(session_id)
    if rate > 30:
        return ChatResponse(
            response="U stuurt te veel berichten. Wacht even en probeer het opnieuw.",
            session_id=session_id, language="nl",
            intent="rate_limited", needs_escalation=False,
        )

    agent = AgentService(qdrant=request.app.state.qdrant, redis=redis)
    result = await agent.process_message(
        message=body.message, session_id=session_id,
        channel=body.channel, mode=mode,
    )

    # Log to PostgreSQL (non-blocking)
    try:
        await request.app.state.db.log_interaction(
            session_id=session_id, channel=body.channel, mode=mode,
            user_message=body.message,
            assistant_message=result.get("response", ""),
            intent=result.get("intent"),
            language=result.get("language", "nl"),
            country=result.get("country", "nl"),
            needs_escalation=result.get("needs_escalation", False),
            sources=result.get("sources"),
        )
    except Exception:
        pass

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
    mode: str = Form(default=MODE_LIMITED),
):
    """Analyze an uploaded document/image and respond in context."""
    redis = request.app.state.redis
    session_id = session_id or str(uuid.uuid4())
    mode = mode if mode in (MODE_LIMITED, MODE_FULL) else MODE_LIMITED

    rate = await redis.increment_rate(session_id)
    if rate > 30:
        return ChatResponse(
            response="U stuurt te veel berichten. Wacht even en probeer het opnieuw.",
            session_id=session_id, language="nl",
            intent="rate_limited", needs_escalation=False,
        )

    doc_svc = DocumentService()
    file_data = await file.read()
    fname = file.filename or "document"

    ok, err = doc_svc.validate_file(fname, len(file_data))
    if not ok:
        return ChatResponse(
            response=f"❌ {err}", session_id=session_id,
            language="en", intent="file_error", needs_escalation=False,
        )

    agent = AgentService(qdrant=request.app.state.qdrant, redis=redis)
    language = await agent.detect_language_with_session(message, session_id)

    try:
        doc = await doc_svc.analyze_document(
            file_data=file_data, filename=fname,
            user_message=message, language=language,
            mode=mode,
        )
    except Exception:
        return ChatResponse(
            response="Er is een fout opgetreden bij het analyseren. Probeer het opnieuw.",
            session_id=session_id, language="nl",
            intent="file_error", needs_escalation=False,
        )

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
# POST /api/chat/voice — voice message transcription + response
# ──────────────────────────────────────────────────────────────────
class VoiceResponse(BaseModel):
    transcription: str
    response: str
    session_id: str
    language: str
    intent: str
    needs_escalation: bool
    sources: list[str] = []
    has_actions: bool = False


@router.post("/voice", response_model=VoiceResponse)
async def chat_voice(
    request: Request,
    file: UploadFile = File(...),
    session_id: Optional[str] = Form(default=None),
    channel: str = Form(default="web"),
    mode: str = Form(default=MODE_LIMITED),
):
    """Transcribe voice audio via Whisper and process as chat message."""
    redis = request.app.state.redis
    session_id = session_id or str(uuid.uuid4())
    mode = mode if mode in (MODE_LIMITED, MODE_FULL) else MODE_LIMITED

    file_data = await file.read()
    if not file_data:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"error": "Empty audio file"})

    if len(file_data) > 25 * 1024 * 1024:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"error": "File too large (max 25 MB)"})

    rate = await redis.increment_rate(session_id)
    if rate > 30:
        return VoiceResponse(
            transcription="", response="U stuurt te veel berichten. Wacht even.",
            session_id=session_id, language="nl",
            intent="rate_limited", needs_escalation=False,
        )

    try:
        transcription = await transcribe_audio(file_data, file.filename or "voice.webm")
    except Exception as e:
        logger.error("Voice transcription failed", error=str(e))
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"error": "Transcription failed"})

    if not transcription:
        return VoiceResponse(
            transcription="", response="Could not recognise speech. Please try again.",
            session_id=session_id, language="en",
            intent="voice_empty", needs_escalation=False,
        )

    agent = AgentService(qdrant=request.app.state.qdrant, redis=redis)
    result = await agent.process_message(
        message=transcription, session_id=session_id,
        channel=channel, mode=mode,
    )

    # Log to PostgreSQL (non-blocking)
    try:
        await request.app.state.db.log_interaction(
            session_id=session_id, channel=channel, mode=mode,
            user_message=f"[voice] {transcription}",
            assistant_message=result.get("response", ""),
            intent=result.get("intent"),
            language=result.get("language", "nl"),
            country=result.get("country", "nl"),
            needs_escalation=result.get("needs_escalation", False),
            sources=result.get("sources"),
        )
    except Exception:
        pass

    return VoiceResponse(
        transcription=transcription,
        **{k: v for k, v in result.items() if k in (
            "response", "session_id", "language", "intent",
            "needs_escalation", "sources", "has_actions",
        )},
    )


# ──────────────────────────────────────────────────────────────────
# POST /api/chat/translate — language learning feature (iOS app)
# ──────────────────────────────────────────────────────────────────
@router.post("/translate", response_model=TranslateResponse)
async def translate_message(request: Request, body: TranslateRequest):
    """Translate text to target language. Used by iOS app for language learning."""
    from openai import AsyncOpenAI
    from app.settings import get_settings

    settings = get_settings()
    lang_names = {
        "nl": "Dutch (Nederlands)", "de": "German (Deutsch)",
        "en": "English", "uk": "Ukrainian (Українська)",
        "ru": "Russian (Русский)", "fr": "French (Français)",
        "es": "Spanish (Español)", "pl": "Polish (Polski)",
        "it": "Italian (Italiano)", "pt": "Portuguese (Português)",
        "tr": "Turkish (Türkçe)",
    }
    target_name = lang_names.get(body.target_language, body.target_language)

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Translate the following text to {target_name}. "
                        "Keep the same formatting (bold, lists, emoji). "
                        "Only return the translation, nothing else."
                    ),
                },
                {"role": "user", "content": body.text},
            ],
            max_tokens=1500, temperature=0.1,
        )
        translated = resp.choices[0].message.content
        logger.info("Translation completed", target=body.target_language, chars=len(body.text))
        return TranslateResponse(translated=translated, target_language=body.target_language)
    except Exception as e:
        logger.error("Translation failed", error=str(e))
        return TranslateResponse(translated=body.text, target_language=body.target_language)


# ──────────────────────────────────────────────────────────────────
@router.delete("/{session_id}")
async def clear_session(request: Request, session_id: str):
    await request.app.state.redis.clear_history(session_id)
    return {"status": "ok", "session_id": session_id}
