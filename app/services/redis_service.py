"""Redis service for session/history management."""

import json
import structlog
import redis.asyncio as aioredis

from app.settings import get_settings

logger = structlog.get_logger()
settings = get_settings()

SESSION_TTL = 3600 * 2  # 2 hours


class RedisService:
    def __init__(self):
        self.redis = None

    async def connect(self):
        self.redis = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            decode_responses=True,
        )
        await self.redis.ping()
        logger.info("Redis connected")

    async def disconnect(self):
        if self.redis:
            await self.redis.close()

    def _key(self, session_id: str) -> str:
        return f"taxnav:session:{session_id}"

    async def get_history(self, session_id: str) -> list[dict]:
        """Get conversation history for a session."""
        raw = await self.redis.get(self._key(session_id))
        if raw:
            return json.loads(raw)
        return []

    async def add_to_history(self, session_id: str, role: str, content: str):
        """Append a message to session history."""
        history = await self.get_history(session_id)
        history.append({"role": role, "content": content})

        # Keep last 20 messages max
        if len(history) > 20:
            history = history[-20:]

        await self.redis.set(
            self._key(session_id),
            json.dumps(history, ensure_ascii=False),
            ex=SESSION_TTL,
        )

    async def clear_history(self, session_id: str):
        await self.redis.delete(self._key(session_id))

    async def increment_rate(self, key: str, window: int = 60) -> int:
        """Simple rate limiter. Returns current count."""
        rkey = f"taxnav:rate:{key}"
        count = await self.redis.incr(rkey)
        if count == 1:
            await self.redis.expire(rkey, window)
        return count
