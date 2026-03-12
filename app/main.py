"""TaxNavigator AI Agent — Main FastAPI Application."""

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Starting TaxNavigator AI Agent...")

    # Initialize services
    app.state.qdrant = QdrantService()
    await app.state.qdrant.ensure_collection()

    app.state.redis = RedisService()
    await app.state.redis.connect()

    logger.info("All services initialized successfully")
    yield

    # Cleanup
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
