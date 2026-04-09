"""Telegram bot webhook router — text + photo/document support.

Supports two bots:
  • Limited bot (TELEGRAM_BOT_TOKEN) — client-facing with restrictions
  • Full bot (TELEGRAM_BOT_TOKEN_FULL) — professional with full capabilities
"""

import json
import structlog
import httpx
from fastapi import APIRouter, Request, Response

from app.services.agent_service import AgentService, MODE_LIMITED, MODE_FULL
from app.services.document_service import DocumentService
from app.settings import get_settings

logger = structlog.get_logger()
settings = get_settings()
router = APIRouter()

TG_BASE = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
TG_BASE_FULL = f"https://api.telegram.org/bot{settings.telegram_bot_token_full}" if settings.telegram_bot_token_full else ""

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf"}


async def _download_telegram_file(file_id: str, client: httpx.AsyncClient, bot_token: str) -> tuple[bytes, str]:
    """Download a file from Telegram by file_id. Returns (file_data, filename)."""
    base = f"https://api.telegram.org/bot{bot_token}"
    resp = await client.get(f"{base}/getFile", params={"file_id": file_id})
    file_info = resp.json().get("result", {})
    file_path = file_info.get("file_path", "")
    filename = file_path.split("/")[-1] if file_path else "document"

    file_resp = await client.get(
        f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
    )
    return file_resp.content, filename


async def _process_file(
    file_data: bytes, filename: str, caption: str,
    chat_id: int, session_id: str, app_state,
    mode: str = MODE_LIMITED,
) -> dict:
    """Analyze uploaded file via DocumentService and return result dict."""
    doc_svc = DocumentService()

    ok, err = doc_svc.validate_file(filename, len(file_data))
    if not ok:
        return {"response": f"❌ {err}", "session_id": session_id,
                "language": "en", "intent": "file_error",
                "needs_escalation": False, "sources": []}

    agent = AgentService(qdrant=app_state.qdrant, redis=app_state.redis)
    language = await agent.detect_language_with_session(caption, session_id)

    doc = await doc_svc.analyze_document(
        file_data=file_data, filename=filename,
        user_message=caption, language=language,
        mode=mode,
    )

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
    """Extract the best file_id from a Telegram message."""
    if message.get("photo"):
        return message["photo"][-1]["file_id"]

    doc = message.get("document")
    if doc:
        mime = doc.get("mime_type", "")
        file_name = doc.get("file_name", "")

        if mime.startswith("image/") or mime == "application/pdf":
            return doc["file_id"]

        from pathlib import Path
        ext = Path(file_name).suffix.lower() if file_name else ""
        if ext in SUPPORTED_EXTENSIONS:
            logger.info("Accepted document by extension", filename=file_name, ext=ext)
            return doc["file_id"]

    return None


async def _handle_telegram_update(
    body: dict,
    request: Request,
    mode: str,
    bot_token: str,
) -> Response:
    """Shared handler for both limited and full bot webhooks."""
    message = body.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    if not chat_id:
        return Response(status_code=200)

    session_prefix = "tg" if mode == MODE_LIMITED else "tgf"
    session_id = f"{session_prefix}_{chat_id}"
    text = message.get("text", "")
    caption = message.get("caption", "")
    file_id = _extract_file_id(message)
    tg_base = f"https://api.telegram.org/bot{bot_token}"

    async with httpx.AsyncClient(timeout=60) as client:
        if file_id:
            try:
                file_data, filename = await _download_telegram_file(file_id, client, bot_token)
                result = await _process_file(
                    file_data, filename, caption,
                    chat_id, session_id, request.app.state,
                    mode=mode,
                )
            except Exception as e:
                logger.error("Telegram file processing error", error=str(e),
                           chat_id=chat_id, mode=mode)
                result = {"response": "Er is een fout opgetreden bij het analyseren van het document. Probeer het opnieuw."}

        elif text:
            agent = AgentService(
                qdrant=request.app.state.qdrant,
                redis=request.app.state.redis,
            )
            result = await agent.process_message(
                message=text, session_id=session_id,
                channel="telegram", mode=mode,
            )
        else:
            return Response(status_code=200)

        await client.post(
            f"{tg_base}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": result["response"],
                "parse_mode": "Markdown",
            },
        )

    # Log to PostgreSQL (non-blocking)
    try:
        await request.app.state.db.log_interaction(
            session_id=session_id, channel="telegram", mode=mode,
            user_message=text or caption or f"[file]",
            assistant_message=result.get("response", ""),
            intent=result.get("intent"),
            language=result.get("language", "nl"),
            country=result.get("country", "nl"),
            needs_escalation=result.get("needs_escalation", False),
            sources=result.get("sources"),
            telegram_id=chat_id,
            telegram_name=message.get("from", {}).get("first_name"),
            telegram_username=message.get("from", {}).get("username"),
        )
    except Exception:
        pass

    logger.info("Telegram message processed", chat_id=chat_id,
                 has_file=bool(file_id), mode=mode)
    return Response(status_code=200)


# ──────────────────────────────────────────────────────────────────
# Webhook endpoints
# ──────────────────────────────────────────────────────────────────

@router.post("/webhook")
async def telegram_webhook(request: Request):
    """Handle incoming Telegram updates — LIMITED bot."""
    try:
        body = await request.json()
        return await _handle_telegram_update(
            body, request, MODE_LIMITED, settings.telegram_bot_token,
        )
    except Exception as e:
        logger.error("Telegram webhook error", error=str(e), mode="limited")
        return Response(status_code=200)


@router.post("/webhook-full")
async def telegram_webhook_full(request: Request):
    """Handle incoming Telegram updates — FULL bot."""
    try:
        body = await request.json()
        return await _handle_telegram_update(
            body, request, MODE_FULL, settings.telegram_bot_token_full,
        )
    except Exception as e:
        logger.error("Telegram webhook error", error=str(e), mode="full")
        return Response(status_code=200)


# ──────────────────────────────────────────────────────────────────
# Setup endpoints
# ──────────────────────────────────────────────────────────────────

@router.get("/setup")
async def setup_webhook():
    """Set up Telegram webhook for LIMITED bot."""
    webhook_url = settings.telegram_webhook_url
    if not webhook_url or not settings.telegram_bot_token:
        return {"error": "Telegram limited bot not configured"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TG_BASE}/setWebhook",
            json={"url": webhook_url},
        )
        return resp.json()


@router.get("/setup-full")
async def setup_webhook_full():
    """Set up Telegram webhook for FULL bot."""
    webhook_url = settings.telegram_webhook_url_full
    if not webhook_url or not settings.telegram_bot_token_full:
        return {"error": "Telegram full bot not configured"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TG_BASE_FULL}/setWebhook",
            json={"url": webhook_url},
        )
        return resp.json()
