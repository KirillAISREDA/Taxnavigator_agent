"""WhatsApp webhook router via Twilio."""

import structlog
from fastapi import APIRouter, Request, Response

from app.services.agent_service import AgentService
from app.settings import get_settings

logger = structlog.get_logger()
settings = get_settings()
router = APIRouter()


@router.post("/webhook")
async def whatsapp_webhook(request: Request):
    """Handle incoming WhatsApp messages via Twilio webhook."""
    try:
        form = await request.form()
        text = form.get("Body", "")
        from_number = form.get("From", "")

        if not text or not from_number:
            return Response(status_code=200)

        # Use phone number as session_id
        session_id = f"wa_{from_number.replace('+', '').replace('whatsapp:', '')}"

        agent = AgentService(
            qdrant=request.app.state.qdrant,
            redis=request.app.state.redis,
        )

        result = await agent.process_message(
            message=text,
            session_id=session_id,
            channel="whatsapp",
        )

        # Send response via Twilio
        from twilio.rest import Client as TwilioClient
        twilio = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
        twilio.messages.create(
            body=result["response"],
            from_=settings.twilio_whatsapp_number,
            to=from_number,
        )

        logger.info("WhatsApp message processed", from_number=from_number[:8] + "***")
        return Response(status_code=200)

    except Exception as e:
        logger.error("WhatsApp webhook error", error=str(e))
        return Response(status_code=200)
