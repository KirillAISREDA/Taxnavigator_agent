"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-small"

    # Qdrant
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    qdrant_collection: str = "taxnav_knowledge"

    # Redis
    redis_host: str = "redis"
    redis_port: int = 6379

    # Telegram
    telegram_bot_token: str = ""
    telegram_webhook_url: str = ""

    # WhatsApp / Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = ""

    # Bitrix24
    bitrix_webhook_url: str = ""

    # App
    app_secret_key: str = "change-me"
    app_base_url: str = "http://localhost:8000"
    allowed_origins: str = "*"
    log_level: str = "INFO"

    # Crawler
    crawler_schedule: str = "0 3 * * *"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
