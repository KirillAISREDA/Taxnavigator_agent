"""Core AI Agent — intent routing, RAG retrieval, response generation."""

import json
import structlog
from openai import AsyncOpenAI
from langdetect import detect

from app.settings import get_settings
from app.services.qdrant_service import QdrantService
from app.services.redis_service import RedisService

logger = structlog.get_logger()
settings = get_settings()


class AgentService:
    """Orchestrates the full agent pipeline: detect language → classify intent → retrieve context → generate response."""

    def __init__(self, qdrant: QdrantService, redis: RedisService):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model
        self.qdrant = qdrant
        self.redis = redis
        self._load_prompts()

    def _load_prompts(self):
        with open("config/prompts.json", "r", encoding="utf-8") as f:
            self.prompts = json.load(f)

    # ------------------------------------------------------------------
    # Language detection
    # ------------------------------------------------------------------
    def detect_language(self, text: str) -> str:
        """Detect user language → nl, uk, ru, en."""
        try:
            lang = detect(text)
            if lang in ("nl", "uk", "ru", "en"):
                return lang
            if lang == "af":  # Afrikaans often confused with Dutch
                return "nl"
            return "en"  # fallback
        except Exception:
            return "en"

    # ------------------------------------------------------------------
    # Intent classification
    # ------------------------------------------------------------------
    async def classify_intent(self, message: str) -> str:
        """Classify user message into an intent category."""
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",  # cheaper model for classification
                messages=[
                    {"role": "system", "content": self.prompts["intent_classification_prompt"]},
                    {"role": "user", "content": message},
                ],
                max_tokens=20,
                temperature=0,
            )
            intent = response.choices[0].message.content.strip().lower()
            logger.info("Intent classified", intent=intent, message=message[:80])
            return intent
        except Exception as e:
            logger.error("Intent classification failed", error=str(e))
            return "other"

    # ------------------------------------------------------------------
    # Category mapping for RAG search
    # ------------------------------------------------------------------
    INTENT_TO_CATEGORIES = {
        "company_info": ["company"],
        "tax_general": ["tax", "legislation"],
        "tax_filing": ["tax"],
        "business_registration": ["business_registration"],
        "subsidies": ["subsidies"],
        "accounting": ["accounting"],
        "reporting": ["reporting_standards", "accounting"],
        "ukrainian_status": ["ukrainian_status", "ukrainian_support"],
        "ukrainian_business": ["ukrainian_status", "business_registration"],
        "double_taxation": ["double_taxation", "tax"],
        "appointment": ["company"],
        "greeting": [],
        "other": [],
    }

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------
    async def process_message(
        self,
        message: str,
        session_id: str,
        channel: str = "web",
    ) -> dict:
        """Full agent pipeline: language → intent → RAG → generate → return."""

        # 1. Detect language
        language = self.detect_language(message)

        # 2. Get conversation history from Redis
        history = await self.redis.get_history(session_id)

        # 3. Classify intent
        intent = await self.classify_intent(message)

        # 4. Retrieve relevant context from Qdrant
        categories = self.INTENT_TO_CATEGORIES.get(intent, [])
        context_chunks = []
        if categories:
            context_chunks = await self.qdrant.search(
                query=message,
                categories=categories,
                limit=5,
            )

        # 5. Build system prompt
        system_prompt = self._build_system_prompt(language, intent, context_chunks)

        # 6. Build messages with history
        messages = [{"role": "system", "content": system_prompt}]
        for h in history[-10:]:  # last 10 messages for context
            messages.append(h)
        messages.append({"role": "user", "content": message})

        # 7. Generate response
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=1000,
            temperature=0.3,
        )
        assistant_message = response.choices[0].message.content

        # 8. Determine if escalation is needed
        needs_escalation = self._check_escalation(intent, assistant_message)

        # 9. Save to history
        await self.redis.add_to_history(session_id, "user", message)
        await self.redis.add_to_history(session_id, "assistant", assistant_message)

        # 10. Log interaction
        logger.info(
            "Message processed",
            session_id=session_id,
            channel=channel,
            language=language,
            intent=intent,
            escalation=needs_escalation,
        )

        return {
            "response": assistant_message,
            "language": language,
            "intent": intent,
            "needs_escalation": needs_escalation,
            "session_id": session_id,
            "sources": [c.get("source_url", "") for c in context_chunks if c.get("source_url")],
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _build_system_prompt(self, language: str, intent: str, context_chunks: list) -> str:
        """Assemble the system prompt with RAG context."""
        base = self.prompts["base_system_prompt"]
        escalation = self.prompts["escalation_prompt"]

        # Add RAG context
        context_text = ""
        if context_chunks:
            context_text = "\n\n## Relevante informatie uit de kennisbank:\n\n"
            for i, chunk in enumerate(context_chunks, 1):
                source = chunk.get("source_name", "Unknown")
                url = chunk.get("source_url", "")
                text = chunk.get("text", "")
                context_text += f"### Bron {i}: {source}\nURL: {url}\n{text}\n\n"

        # Language instruction
        lang_map = {
            "nl": "Antwoord in het Nederlands.",
            "uk": "Відповідай українською мовою.",
            "ru": "Отвечай на русском языке.",
            "en": "Respond in English.",
        }
        lang_instruction = lang_map.get(language, lang_map["en"])

        return f"{base}\n\n{escalation}\n\n{lang_instruction}\n{context_text}"

    def _check_escalation(self, intent: str, response: str) -> bool:
        """Check if this conversation should be escalated to a human."""
        escalation_intents = {
            "appointment", "double_taxation", "ukrainian_business",
        }
        if intent in escalation_intents:
            return True
        # Check if the response itself suggests escalation
        escalation_markers = [
            "afspraak", "specialist", "консультац", "запис",
            "appointment", "consultation",
        ]
        return any(marker in response.lower() for marker in escalation_markers)
