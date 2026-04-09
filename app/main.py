"""TaxNavigator AI Agent — Main FastAPI Application.

Supports two Telegram bots:
  • Limited bot (TELEGRAM_BOT_TOKEN) — client-facing
  • Full bot (TELEGRAM_BOT_TOKEN_FULL) — professional
"""

import asyncio
import json
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from app.settings import get_settings
from app.services.qdrant_service import QdrantService
from app.services.redis_service import RedisService
from app.services.agent_service import MODE_LIMITED, MODE_FULL
from app.routers import chat, telegram, whatsapp, widget, health

logger = structlog.get_logger()
settings = get_settings()


async def _telegram_polling(app: FastAPI, bot_token: str, mode: str):
    """Poll Telegram for updates (used when webhook is not configured)."""
    import httpx
    from app.services.agent_service import AgentService
    from app.services.document_service import DocumentService
    from app.routers.telegram import _download_telegram_file, _extract_file_id, _process_file

    base = f"https://api.telegram.org/bot{bot_token}"
    offset = 0
    session_prefix = "tg" if mode == MODE_LIMITED else "tgf"
    mode_label = "limited" if mode == MODE_LIMITED else "full"

    logger.info(f"Telegram polling started ({mode_label})")

    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            try:
                resp = await client.get(
                    f"{base}/getUpdates",
                    params={"offset": offset, "timeout": 30},
                )
                data = resp.json()
                if not data.get("ok"):
                    logger.warning(f"Telegram getUpdates error ({mode_label})", data=data)
                    await asyncio.sleep(5)
                    continue

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    text = message.get("text", "")
                    caption = message.get("caption", "")
                    chat_id = message.get("chat", {}).get("id")

                    if not chat_id:
                        continue

                    file_id = _extract_file_id(message)

                    if not text and not file_id:
                        continue

                    session_id = f"{session_prefix}_{chat_id}"

                    try:
                        if file_id:
                            file_data, filename = await _download_telegram_file(
                                file_id, client, bot_token,
                            )
                            result = await _process_file(
                                file_data, filename, caption,
                                chat_id, session_id, app.state,
                                mode=mode,
                            )
                        else:
                            agent = AgentService(
                                qdrant=app.state.qdrant,
                                redis=app.state.redis,
                            )
                            result = await agent.process_message(
                                message=text,
                                session_id=session_id,
                                channel="telegram",
                                mode=mode,
                            )

                        await client.post(
                            f"{base}/sendMessage",
                            json={
                                "chat_id": chat_id,
                                "text": result["response"],
                                "parse_mode": "Markdown",
                            },
                        )
                        logger.info(f"Telegram polling ({mode_label}): message processed",
                                    chat_id=chat_id, has_file=bool(file_id))
                    except Exception as e:
                        logger.error(f"Telegram polling ({mode_label}): message error",
                                    error=str(e))

            except asyncio.CancelledError:
                logger.info(f"Telegram polling stopped ({mode_label})")
                return
            except Exception as e:
                logger.warning(f"Telegram polling error ({mode_label})", error=str(e))
                await asyncio.sleep(5)


def _should_use_polling(webhook_url: str) -> bool:
    """Check if we should use polling (no HTTPS webhook configured)."""
    return not webhook_url or not webhook_url.startswith("https")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Starting TaxNavigator AI Agent...")

    # Initialize services
    app.state.qdrant = QdrantService()
    await app.state.qdrant.ensure_collection()

    app.state.redis = RedisService()
    await app.state.redis.connect()

    polling_tasks = []
    poll_locks = []

    # ── Limited bot polling ───────────────────────────────────────
    if settings.telegram_bot_token and _should_use_polling(settings.telegram_webhook_url):
        import fcntl
        try:
            lock = open("/tmp/taxnav_tg_poll_limited.lock", "w")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            poll_locks.append(lock)
            task = asyncio.create_task(
                _telegram_polling(app, settings.telegram_bot_token, MODE_LIMITED)
            )
            polling_tasks.append(task)
            logger.info("Limited bot: polling mode")
        except (IOError, OSError):
            logger.info("Limited bot: another worker holds the lock, skipping")

    # ── Full bot polling ──────────────────────────────────────────
    if settings.telegram_bot_token_full and _should_use_polling(settings.telegram_webhook_url_full):
        import fcntl
        try:
            lock = open("/tmp/taxnav_tg_poll_full.lock", "w")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            poll_locks.append(lock)
            task = asyncio.create_task(
                _telegram_polling(app, settings.telegram_bot_token_full, MODE_FULL)
            )
            polling_tasks.append(task)
            logger.info("Full bot: polling mode")
        except (IOError, OSError):
            logger.info("Full bot: another worker holds the lock, skipping")

    logger.info("All services initialized successfully",
                limited_bot=bool(settings.telegram_bot_token),
                full_bot=bool(settings.telegram_bot_token_full))
    yield

    # Cleanup
    for task in polling_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    for lock in poll_locks:
        lock.close()
    await app.state.redis.disconnect()
    logger.info("TaxNavigator AI Agent shut down")


app = FastAPI(
    title="TaxNavigator AI Agent",
    version="1.1.0",
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


# Security headers middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if "/widget" in str(request.url):
            response.headers["Content-Security-Policy"] = (
                "frame-ancestors 'self' https://taxnavigator-advice.nl https://www.taxnavigator-advice.nl"
            )
        return response

app.add_middleware(SecurityHeadersMiddleware)

# Static files and templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Routers
app.include_router(health.router, tags=["health"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(widget.router, prefix="/widget", tags=["widget"])
app.include_router(telegram.router, prefix="/api/telegram", tags=["telegram"])
app.include_router(whatsapp.router, prefix="/api/whatsapp", tags=["whatsapp"])
