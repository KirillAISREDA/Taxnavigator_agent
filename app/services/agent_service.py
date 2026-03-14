"""Core AI Agent — intent routing, RAG retrieval, response generation.
Changes vs original:
  • _build_system_prompt now injects the anti_diy_prompt from prompts.json
  • _check_escalation expanded with tax_filing, business_registration
"""

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

    def __init__(self, qdrant: QdrantService, redis: RedisService):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model
        self.qdrant = qdrant
        self.redis = redis
        self._load_prompts()

    def _load_prompts(self):
        with open("config/prompts.json", "r", encoding="utf-8") as f:
            self.prompts = json.load(f)

    # ── language ──────────────────────────────────────────────────
    def detect_language(self, text: str) -> str:
        try:
            lang = detect(text)
            if lang in ("nl", "uk", "ru", "en"):
                return lang
            if lang == "af":
                return "nl"
            return "en"
        except Exception:
            return "en"

    # ── intent ────────────────────────────────────────────────────
    async def classify_intent(self, message: str) -> str:
        try:
            resp = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": self.prompts["intent_classification_prompt"]},
                    {"role": "user", "content": message},
                ],
                max_tokens=20, temperature=0,
            )
            intent = resp.choices[0].message.content.strip().lower()
            logger.info("Intent classified", intent=intent, message=message[:80])
            return intent
        except Exception as e:
            logger.error("Intent classification failed", error=str(e))
            return "other"

    INTENT_TO_CATEGORIES = {
        "company_info":          ["company"],
        "tax_general":           ["tax", "legislation"],
        "tax_filing":            ["tax"],
        "business_registration": ["business_registration"],
        "subsidies":             ["subsidies"],
        "accounting":            ["accounting"],
        "reporting":             ["reporting_standards", "accounting"],
        "ukrainian_status":      ["ukrainian_status", "ukrainian_support"],
        "ukrainian_business":    ["ukrainian_status", "business_registration"],
        "double_taxation":       ["double_taxation", "tax"],
        "appointment":           ["company"],
        "greeting":              [],
        "other":                 [],
    }

    # ── full pipeline ─────────────────────────────────────────────
    async def process_message(
        self, message: str, session_id: str, channel: str = "web",
    ) -> dict:
        language = self.detect_language(message)
        history = await self.redis.get_history(session_id)
        intent = await self.classify_intent(message)

        categories = self.INTENT_TO_CATEGORIES.get(intent, [])
        context_chunks = []
        if categories:
            context_chunks = await self.qdrant.search(
                query=message, categories=categories, limit=5,
            )

        system_prompt = self._build_system_prompt(language, intent, context_chunks)

        messages = [{"role": "system", "content": system_prompt}]
        for h in history[-10:]:
            messages.append(h)
        messages.append({"role": "user", "content": message})

        resp = await self.client.chat.completions.create(
            model=self.model, messages=messages,
            max_tokens=1000, temperature=0.3,
        )
        assistant_message = resp.choices[0].message.content
        needs_escalation = self._check_escalation(intent, assistant_message)

        await self.redis.add_to_history(session_id, "user", message)
        await self.redis.add_to_history(session_id, "assistant", assistant_message)

        logger.info("Message processed",
                    session_id=session_id, channel=channel,
                    language=language, intent=intent, escalation=needs_escalation)

        return {
            "response": assistant_message,
            "language": language,
            "intent": intent,
            "needs_escalation": needs_escalation,
            "session_id": session_id,
            "sources": [c.get("source_url", "") for c in context_chunks if c.get("source_url")],
        }

    # ── helpers ────────────────────────────────────────────────────
    def _build_system_prompt(self, language: str, intent: str, chunks: list) -> str:
        base = self.prompts["base_system_prompt"]
        escalation = self.prompts["escalation_prompt"]
        anti_diy = self.prompts.get("anti_diy_prompt", "")

        context = ""
        if chunks:
            context = "\n\n## Relevante informatie uit de kennisbank:\n\n"
            for i, c in enumerate(chunks, 1):
                context += (f"### Bron {i}: {c.get('source_name','')}\n"
                            f"URL: {c.get('source_url','')}\n{c.get('text','')}\n\n")

        lang_map = {
            "nl": "Antwoord in het Nederlands.",
            "uk": "Відповідай українською мовою.",
            "ru": "Отвечай на русском языке.",
            "en": "Respond in English.",
        }
        return f"{base}\n\n{escalation}\n\n{anti_diy}\n\n{lang_map.get(language, lang_map['en'])}\n{context}"

    def _check_escalation(self, intent: str, response: str) -> bool:
        if intent in {"appointment", "double_taxation", "ukrainian_business",
                       "tax_filing", "business_registration"}:
            return True
        markers = ["afspraak", "specialist", "консультац", "запис",
                    "appointment", "consultation"]
        return any(m in response.lower() for m in markers)
