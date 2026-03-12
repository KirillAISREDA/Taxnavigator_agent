"""TaxNavigator AI Agent — Main FastAPI Application."""

import asyncio
import json
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.settings import get_settings
from app.services.qdrant_service import QdrantService
from app.services.redis_service import RedisService
from app.routers import chat, telegram, whatsapp, widget, health

logger = structlog.get_logger()
settings = get_settings()


async def _telegram_polling(app: FastAPI):
    """Poll Telegram for updates when webhook is not configured (no HTTPS)."""
    import httpx
    from app.services.agent_service import AgentService

    token = settings.telegram_bot_token
    base = f"https://api.telegram.org/bot{token}"
    offset = 0

    logger.info("Telegram polling started")

    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            try:
                resp = await client.get(
                    f"{base}/getUpdates",
                    params={"offset": offset, "timeout": 30},
                )
                data = resp.json()
                if not data.get("ok"):
                    logger.warning("Telegram getUpdates error", data=data)
                    await asyncio.sleep(5)
                    continue

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    text = message.get("text", "")
                    chat_id = message.get("chat", {}).get("id")

                    if not text or not chat_id:
                        continue

                    try:
                        agent = AgentService(
                            qdrant=app.state.qdrant,
                            redis=app.state.redis,
                        )
                        result = await agent.process_message(
                            message=text,
                            session_id=f"tg_{chat_id}",
                            channel="telegram",
                        )
                        await client.post(
                            f"{base}/sendMessage",
                            json={
                                "chat_id": chat_id,
                                "text": result["response"],
                                "parse_mode": "Markdown",
                            },
                        )
                        logger.info("Telegram polling: message processed", chat_id=chat_id)
                    except Exception as e:
                        logger.error("Telegram polling: message error", error=str(e))

            except asyncio.CancelledError:
                logger.info("Telegram polling stopped")
                return
            except Exception as e:
                logger.warning("Telegram polling error", error=str(e))
                await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Starting TaxNavigator AI Agent...")

    # Initialize services
    app.state.qdrant = QdrantService()
    await app.state.qdrant.ensure_collection()

    app.state.redis = RedisService()
    await app.state.redis.connect()

    # Start Telegram polling if token is set but no HTTPS webhook
    polling_task = None
    if settings.telegram_bot_token and not settings.telegram_webhook_url.startswith("https"):
        polling_task = asyncio.create_task(_telegram_polling(app))

    logger.info("All services initialized successfully")
    yield

    # Cleanup
    if polling_task:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    await app.state.redis.disconnect()
    logger.info("TaxNavigator AI Agent shut down")


app = FastAPI(
    title="TaxNavigator AI Agent",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS for widget embedding
origins = settings.allowed_origins.split(",") if settings.allowed_origins != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files and templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Routers
app.include_router(health.router, tags=["health"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(widget.router, prefix="/widget", tags=["widget"])
app.include_router(telegram.router, prefix="/api/telegram", tags=["telegram"])
app.include_router(whatsapp.router, prefix="/api/whatsapp", tags=["whatsapp"])
