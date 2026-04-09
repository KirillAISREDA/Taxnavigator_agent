"""PostgreSQL service — clients, conversations, messages, cached responses."""

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text, ForeignKey,
    JSON, BigInteger, Index, text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, relationship

from app.settings import get_settings

logger = structlog.get_logger()
settings = get_settings()


# ── Models ────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class Client(Base):
    __tablename__ = "clients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_id = Column(BigInteger, unique=True, nullable=True, index=True)
    whatsapp_id = Column(String(50), unique=True, nullable=True, index=True)
    web_session_id = Column(String(100), nullable=True)
    name = Column(String(200), nullable=True)
    username = Column(String(100), nullable=True)
    phone = Column(String(30), nullable=True)
    language = Column(String(5), default="nl")
    country = Column(String(5), default="nl")
    channel = Column(String(20), default="web")
    mode = Column(String(10), default="limited")
    first_seen_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    total_messages = Column(Integer, default=0)
    metadata_ = Column("metadata", JSONB, default=dict)

    conversations = relationship("Conversation", back_populates="client")


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True)
    session_id = Column(String(100), nullable=False, index=True)
    channel = Column(String(20), default="web")
    mode = Column(String(10), default="limited")
    country = Column(String(5), default="nl")
    started_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    ended_at = Column(DateTime(timezone=True), nullable=True)
    message_count = Column(Integer, default=0)

    client = relationship("Client", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False, index=True)
    role = Column(String(10), nullable=False)  # user / assistant
    content = Column(Text, nullable=False)
    intent = Column(String(50), nullable=True)
    language = Column(String(5), nullable=True)
    country = Column(String(5), nullable=True)
    needs_escalation = Column(Boolean, default=False)
    sources = Column(JSONB, default=list)
    tokens_used = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    conversation = relationship("Conversation", back_populates="messages")

    __table_args__ = (
        Index("ix_messages_created_at", "created_at"),
    )


class CachedResponse(Base):
    __tablename__ = "cached_responses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_hash = Column(String(32), nullable=False, index=True)
    question_text = Column(Text, nullable=False)
    response_text = Column(Text, nullable=False)
    intent = Column(String(50), nullable=True)
    country = Column(String(5), nullable=True)
    language = Column(String(5), nullable=True)
    hit_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=True)


# ── Service ───────────────────────────────────────────────────────

class DBService:
    def __init__(self):
        url = (
            f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
            f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
        )
        self.engine = create_async_engine(url, pool_size=5, max_overflow=5)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init_db(self):
        """Create tables if they don't exist."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("PostgreSQL tables initialized")

    async def close(self):
        await self.engine.dispose()

    # ── Client operations ─────────────────────────────────────────

    async def get_or_create_client(
        self,
        telegram_id: int | None = None,
        whatsapp_id: str | None = None,
        web_session_id: str | None = None,
        name: str | None = None,
        username: str | None = None,
        phone: str | None = None,
        language: str = "nl",
        country: str = "nl",
        channel: str = "web",
        mode: str = "limited",
        metadata: dict | None = None,
    ) -> Client:
        async with self.Session() as session:
            client = None

            if telegram_id:
                result = await session.execute(
                    text("SELECT * FROM clients WHERE telegram_id = :tid"),
                    {"tid": telegram_id},
                )
                row = result.mappings().first()
                if row:
                    client_id = row["id"]
                    await session.execute(
                        text("""UPDATE clients SET
                            last_seen_at = :now, language = :lang, country = :country,
                            name = COALESCE(:name, name), username = COALESCE(:username, username),
                            mode = :mode, total_messages = total_messages + 1
                            WHERE id = :id"""),
                        {"now": datetime.now(timezone.utc), "lang": language, "country": country,
                         "name": name, "username": username, "mode": mode, "id": client_id},
                    )
                    await session.commit()
                    result2 = await session.execute(
                        text("SELECT * FROM clients WHERE id = :id"), {"id": client_id}
                    )
                    return result2.mappings().first()

            elif whatsapp_id:
                result = await session.execute(
                    text("SELECT * FROM clients WHERE whatsapp_id = :wid"),
                    {"wid": whatsapp_id},
                )
                row = result.mappings().first()
                if row:
                    client_id = row["id"]
                    await session.execute(
                        text("""UPDATE clients SET last_seen_at = :now, language = :lang,
                            total_messages = total_messages + 1 WHERE id = :id"""),
                        {"now": datetime.now(timezone.utc), "lang": language, "id": client_id},
                    )
                    await session.commit()
                    result2 = await session.execute(
                        text("SELECT * FROM clients WHERE id = :id"), {"id": client_id}
                    )
                    return result2.mappings().first()

            # Create new client
            new_id = uuid.uuid4()
            now = datetime.now(timezone.utc)
            await session.execute(
                text("""INSERT INTO clients
                    (id, telegram_id, whatsapp_id, web_session_id, name, username, phone,
                     language, country, channel, mode, first_seen_at, last_seen_at, total_messages, metadata)
                    VALUES (:id, :tid, :wid, :wsid, :name, :username, :phone,
                     :lang, :country, :channel, :mode, :now, :now, 1, :meta)"""),
                {"id": new_id, "tid": telegram_id, "wid": whatsapp_id, "wsid": web_session_id,
                 "name": name, "username": username, "phone": phone,
                 "lang": language, "country": country, "channel": channel, "mode": mode,
                 "now": now, "meta": metadata or {}},
            )
            await session.commit()
            result = await session.execute(
                text("SELECT * FROM clients WHERE id = :id"), {"id": new_id}
            )
            return result.mappings().first()

    # ── Conversation operations ───────────────────────────────────

    async def get_or_create_conversation(
        self, client_id, session_id: str,
        channel: str = "web", mode: str = "limited", country: str = "nl",
    ):
        async with self.Session() as session:
            result = await session.execute(
                text("SELECT * FROM conversations WHERE session_id = :sid ORDER BY started_at DESC LIMIT 1"),
                {"sid": session_id},
            )
            row = result.mappings().first()
            if row:
                return row

            new_id = uuid.uuid4()
            await session.execute(
                text("""INSERT INTO conversations
                    (id, client_id, session_id, channel, mode, country, started_at, message_count)
                    VALUES (:id, :cid, :sid, :channel, :mode, :country, :now, 0)"""),
                {"id": new_id, "cid": client_id, "sid": session_id,
                 "channel": channel, "mode": mode, "country": country,
                 "now": datetime.now(timezone.utc)},
            )
            await session.commit()
            result = await session.execute(
                text("SELECT * FROM conversations WHERE id = :id"), {"id": new_id}
            )
            return result.mappings().first()

    # ── Message operations ────────────────────────────────────────

    async def save_message(
        self,
        conversation_id,
        role: str,
        content: str,
        intent: str | None = None,
        language: str | None = None,
        country: str | None = None,
        needs_escalation: bool = False,
        sources: list | None = None,
        tokens_used: int | None = None,
    ):
        async with self.Session() as session:
            msg_id = uuid.uuid4()
            await session.execute(
                text("""INSERT INTO messages
                    (id, conversation_id, role, content, intent, language, country,
                     needs_escalation, sources, tokens_used, created_at)
                    VALUES (:id, :cid, :role, :content, :intent, :lang, :country,
                     :esc, :sources, :tokens, :now)"""),
                {"id": msg_id, "cid": conversation_id, "role": role, "content": content,
                 "intent": intent, "lang": language, "country": country,
                 "esc": needs_escalation, "sources": sources or [],
                 "tokens": tokens_used, "now": datetime.now(timezone.utc)},
            )
            await session.execute(
                text("UPDATE conversations SET message_count = message_count + 1, ended_at = :now WHERE id = :id"),
                {"now": datetime.now(timezone.utc), "id": conversation_id},
            )
            await session.commit()

    # ── Logging helper (fire-and-forget) ──────────────────────────

    async def log_interaction(
        self,
        session_id: str,
        channel: str,
        mode: str,
        user_message: str,
        assistant_message: str,
        intent: str | None = None,
        language: str = "nl",
        country: str = "nl",
        needs_escalation: bool = False,
        sources: list | None = None,
        telegram_id: int | None = None,
        telegram_name: str | None = None,
        telegram_username: str | None = None,
        whatsapp_id: str | None = None,
    ):
        """One-call method to log a full interaction. Non-blocking — errors are caught."""
        try:
            client = await self.get_or_create_client(
                telegram_id=telegram_id,
                whatsapp_id=whatsapp_id,
                web_session_id=session_id if not telegram_id and not whatsapp_id else None,
                name=telegram_name,
                username=telegram_username,
                language=language,
                country=country,
                channel=channel,
                mode=mode,
            )
            conv = await self.get_or_create_conversation(
                client_id=client["id"],
                session_id=session_id,
                channel=channel,
                mode=mode,
                country=country,
            )
            await self.save_message(
                conversation_id=conv["id"],
                role="user", content=user_message,
                intent=intent, language=language, country=country,
            )
            await self.save_message(
                conversation_id=conv["id"],
                role="assistant", content=assistant_message,
                intent=intent, language=language, country=country,
                needs_escalation=needs_escalation, sources=sources,
            )
        except Exception as e:
            logger.error("DB log_interaction failed", error=str(e))
