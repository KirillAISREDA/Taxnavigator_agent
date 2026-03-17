"""Telegram bot webhook router — text + photo/document support."""

import json
import structlog
import httpx
from fastapi import APIRouter, Request, Response

from app.services.agent_service import AgentService
from app.services.document_service import DocumentService
from app.settings import get_settings

logger = structlog.get_logger()
settings = get_settings()
router = APIRouter()

TG_BASE = f"https://api.telegram.org/bot{settings.telegram_bot_token}"


async def _download_telegram_file(file_id: str, client: httpx.AsyncClient) -> tuple[bytes, str]:
    """Download a file from Telegram by file_id. Returns (file_data, filename)."""
    resp = await client.get(f"{TG_BASE}/getFile", params={"file_id": file_id})
    file_info = resp.json().get("result", {})
    file_path = file_info.get("file_path", "")
    filename = file_path.split("/")[-1] if file_path else "document"

    file_resp = await client.get(
        f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"
    )
    return file_resp.content, filename


async def _process_file(
    file_data: bytes, filename: str, caption: str,
    chat_id: int, session_id: str, app_state,
) -> dict:
    """Analyze uploaded file via DocumentService and return result dict."""
    doc_svc = DocumentService()

    ok, err = doc_svc.validate_file(filename, len(file_data))
    if not ok:
        return {"response": f"❌ {err}", "session_id": session_id,
                "language": "en", "intent": "file_error",
                "needs_escalation": False, "sources": []}

    # ── Detect language from SESSION HISTORY, not just the caption ──
    agent = AgentService(qdrant=app_state.qdrant, redis=app_state.redis)
    language = await agent.detect_language_with_session(caption, session_id)

    doc = await doc_svc.analyze_document(
        file_data=file_data, filename=filename,
        user_message=caption, language=language,
    )

    # RAG enrichment
    cat_map = {
        "tax_assessment": ["tax"], "tax_letter": ["tax"],
        "kvk_extract": ["business_registration"],
        "residence_permit": ["ukrainian_status"],
        "toeslagen": ["tax", "ukrainian_support"],
    }
    sources = []
    categories = cat_map.get(doc["document_type"], [])
    if categories:
        chunks = await agent.qdrant.search(
            query=caption or doc["document_type"], categories=categories, limit=3,
        )
        sources = [c["source_url"] for c in chunks if c.get("source_url")]

    # Save to history
    user_note = f"[📄 {filename}]"
    if caption:
        user_note += f"\n{caption}"
    await app_state.redis.add_to_history(session_id, "user", user_note)
    await app_state.redis.add_to_history(session_id, "assistant", doc["analysis"])

    return {
        "response": doc["analysis"],
        "session_id": session_id,
        "language": language,
        "intent": f"document_{doc['document_type']}",
        "needs_escalation": doc["needs_escalation"],
        "sources": sources,
    }


def _extract_file_id(message: dict) -> str | None:
    """Extract the best file_id from a Telegram message (photo or document)."""
    if message.get("photo"):
        return message["photo"][-1]["file_id"]
    doc = message.get("document")
    if doc:
        mime = doc.get("mime_type", "")
        if mime.startswith("image/") or mime == "application/pdf":
            return doc["file_id"]
    return None


@router.post("/webhook")
async def telegram_webhook(request: Request):
    """Handle incoming Telegram updates via webhook."""
    try:
        body = await request.json()
        message = body.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        if not chat_id:
            return Response(status_code=200)

        session_id = f"tg_{chat_id}"
        text = message.get("text", "")
        caption = message.get("caption", "")
        file_id = _extract_file_id(message)

        async with httpx.AsyncClient(timeout=30) as client:
            if file_id:
                # ── photo / document ──────────────────────────────
                try:
                    file_data, filename = await _download_telegram_file(file_id, client)
                    result = await _process_file(
                        file_data, filename, caption,
                        chat_id, session_id, request.app.state,
                    )
                except Exception as e:
                    logger.error("Telegram file processing error", error=str(e), chat_id=chat_id)
                    result = {"response": "Er is een fout opgetreden bij het analyseren van het document. Probeer het opnieuw."}

            elif text:
                # ── text only ─────────────────────────────────────
                agent = AgentService(
                    qdrant=request.app.state.qdrant,
                    redis=request.app.state.redis,
                )
                result = await agent.process_message(
                    message=text, session_id=session_id, channel="telegram",
                )
            else:
                return Response(status_code=200)

            await client.post(
                f"{TG_BASE}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": result["response"],
                    "parse_mode": "Markdown",
                },
            )

        logger.info("Telegram message processed", chat_id=chat_id,
                     has_file=bool(file_id))
        return Response(status_code=200)

    except Exception as e:
        logger.error("Telegram webhook error", error=str(e))
        return Response(status_code=200)


@router.get("/setup")
async def setup_webhook():
    """Set up Telegram webhook (call once during deployment)."""
    webhook_url = settings.telegram_webhook_url
    if not webhook_url or not settings.telegram_bot_token:
        return {"error": "Telegram not configured"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TG_BASE}/setWebhook",
            json={"url": webhook_url},
        )
        return resp.json()
