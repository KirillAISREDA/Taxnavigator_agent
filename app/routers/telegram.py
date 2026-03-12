"""Telegram bot webhook router."""

import json
import structlog
from fastapi import APIRouter, Request, Response

from app.services.agent_service import AgentService
from app.settings import get_settings

logger = structlog.get_logger()
settings = get_settings()
router = APIRouter()


@router.post("/webhook")
async def telegram_webhook(request: Request):
    """Handle incoming Telegram updates via webhook."""
    try:
        body = await request.json()
        message = body.get("message", {})
        text = message.get("text", "")
        chat_id = message.get("chat", {}).get("id")

        if not text or not chat_id:
            return Response(status_code=200)

        # Use chat_id as session_id for Telegram
        session_id = f"tg_{chat_id}"

        agent = AgentService(
            qdrant=request.app.state.qdrant,
            redis=request.app.state.redis,
        )

        result = await agent.process_message(
            message=text,
            session_id=session_id,
            channel="telegram",
        )

        # Send response back via Telegram API
        import httpx
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": result["response"],
                    "parse_mode": "Markdown",
                },
            )

        logger.info("Telegram message processed", chat_id=chat_id)
        return Response(status_code=200)

    except Exception as e:
        logger.error("Telegram webhook error", error=str(e))
        return Response(status_code=200)  # Always 200 to avoid retries


@router.get("/setup")
async def setup_webhook():
    """Set up Telegram webhook (call once during deployment)."""
    import httpx
    webhook_url = settings.telegram_webhook_url
    if not webhook_url or not settings.telegram_bot_token:
        return {"error": "Telegram not configured"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook",
            json={"url": webhook_url},
        )
        return resp.json()
