"""Core AI Agent — intent routing, RAG retrieval, response generation.

Supports two modes:
  • "limited" — client-facing bot, NL only, with anti-DIY restrictions
  • "full"    — professional bot, multi-country (NL/DE/IT/FR/ES), full capabilities

Multi-country logic (FULL mode only):
  • detect_country() via GPT-4o-mini — determines which country the question is about
  • Country persisted in Redis session — no re-detection needed each message
  • Country context injected into system prompt from countries.json
  • Qdrant search filtered by country
  • Language detection extended: 27 languages
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

MODE_LIMITED = "limited"
MODE_FULL = "full"

SUPPORTED_COUNTRIES = {"nl", "de", "it", "fr", "es"}
DEFAULT_COUNTRY = "nl"

# Language → likely country (heuristic fallback, NOT authoritative)
LANG_TO_COUNTRY_HINT = {
    "nl": "nl", "de": "de", "it": "it", "fr": "fr", "es": "es",
    "tr": "de",   # Most Turkish speakers in EU are in Germany
    "pl": "de",   # Large Polish diaspora in Germany
    "ro": "it",   # Large Romanian diaspora in Italy/Spain
    "bg": "de",   # Bulgarian diaspora spread across DE/ES
    "hr": "de",   # Croatian diaspora in Germany/Austria
    "sr": "de",   # Serbian diaspora in Germany/Austria
    "bs": "de",   # Bosnian diaspora in Germany/Austria
    "sq": "it",   # Albanian diaspora in Italy
    "pt": "fr",   # Portuguese diaspora in France
    "ar": "fr",   # Maghreb Arabic diaspora in France
    "id": "nl",   # Indonesian diaspora in Netherlands
}


class AgentService:

    def __init__(self, qdrant: QdrantService, redis: RedisService):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model
        self.qdrant = qdrant
        self.redis = redis
        self._load_prompts()
        self._load_countries()

    def _load_prompts(self):
        with open("config/prompts.json", "r", encoding="utf-8") as f:
            self.prompts = json.load(f)

    def _load_countries(self):
        try:
            with open("config/countries.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                self.countries = data.get("countries", data)
        except FileNotFoundError:
            logger.warning("countries.json not found, using empty config")
            self.countries = {}

    # ══════════════════════════════════════════════════════════════
    # LANGUAGE DETECTION
    # ══════════════════════════════════════════════════════════════

    SUPPORTED_LANGS = {
        "nl", "en", "de", "fr", "it", "es", "uk", "ru",
        "tr", "ar", "pl", "ro", "zh", "pt",
        "hr", "sr", "bs", "bg", "sq", "bn", "ur", "pa", "hi", "tl",
        "id",
    }

    LANG_ALIASES = {
        "af": "nl", "ca": "es", "zh-cn": "zh", "zh-tw": "zh",
        "mk": "bg", "fil": "tl",
    }

    def detect_language(self, text: str) -> str:
        try:
            lang = detect(text)
            lang = self.LANG_ALIASES.get(lang, lang)
            if lang in self.SUPPORTED_LANGS:
                return lang
            return "en"
        except Exception:
            return "en"

    async def detect_language_with_session(
        self, text: str | None, session_id: str,
    ) -> str:
        if text and len(text.strip()) >= 8:
            lang = self.detect_language(text)
            if lang:
                return lang

        try:
            history = await self.redis.get_history(session_id)
            for msg in reversed(history):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    lines = [
                        l for l in content.split("\n")
                        if l.strip() and not l.strip().startswith("[📄")
                    ]
                    user_text = " ".join(lines).strip()
                    if len(user_text) >= 4:
                        lang = self.detect_language(user_text)
                        if lang:
                            return lang
        except Exception:
            pass

        if text and text.strip():
            lang = self.detect_language(text)
            if lang:
                return lang

        return "nl"

    # ══════════════════════════════════════════════════════════════
    # COUNTRY DETECTION (FULL MODE ONLY)
    # ══════════════════════════════════════════════════════════════

    async def detect_country(self, message: str, session_id: str) -> str:
        session_country = await self.redis.get_session_country(session_id)
        if session_country and session_country in SUPPORTED_COUNTRIES:
            override = await self._classify_country_gpt(message)
            if override in SUPPORTED_COUNTRIES and override != session_country:
                await self.redis.set_session_country(session_id, override)
                logger.info("Country changed", old=session_country, new=override,
                           session=session_id)
                return override
            return session_country

        detected = await self._classify_country_gpt(message)
        if detected in SUPPORTED_COUNTRIES:
            await self.redis.set_session_country(session_id, detected)
            return detected

        lang = self.detect_language(message)
        hint = LANG_TO_COUNTRY_HINT.get(lang)
        if hint and hint in SUPPORTED_COUNTRIES:
            await self.redis.set_session_country(session_id, hint)
            return hint

        await self.redis.set_session_country(session_id, DEFAULT_COUNTRY)
        return DEFAULT_COUNTRY

    async def _classify_country_gpt(self, message: str) -> str:
        try:
            resp = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": self.prompts["country_detection_prompt"]},
                    {"role": "user", "content": message},
                ],
                max_tokens=10, temperature=0,
            )
            country = resp.choices[0].message.content.strip().lower()
            logger.info("Country classified", country=country, message=message[:60])
            return country
        except Exception as e:
            logger.error("Country classification failed", error=str(e))
            return "unknown"

    def _get_country_context(self, country_code: str) -> str:
        c = self.countries.get(country_code)
        if not c:
            return f"Country: {country_code.upper()} (no detailed config available)"

        lines = [
            f"Country: {c.get('name_en', country_code.upper())} ({c.get('name_local', '')})",
            f"Tax authority: {c['tax_authority']['name']} — {c['tax_authority']['website']}",
            f"Business registry: {c['business_registry']['name']} — {c['business_registry']['website']}",
            f"Income tax: {c['income_tax']['name']}",
        ]
        for bracket in c['income_tax']['rates']:
            lines.append(f"  - {bracket['bracket']}: {bracket['rate']}")
        if c['income_tax'].get('note'):
            lines.append(f"  Note: {c['income_tax']['note']}")
        lines.append(f"  Filing deadline: {c['income_tax']['filing_deadline']}")

        lines.append(f"Corporate tax: {c['corporate_tax']['name']} — {c['corporate_tax']['standard_rate']}")
        if c['corporate_tax'].get('reduced_rate'):
            lines.append(f"  Reduced: {c['corporate_tax']['reduced_rate']}")
        if c['corporate_tax'].get('note'):
            lines.append(f"  Note: {c['corporate_tax']['note']}")

        lines.append(f"VAT: {c['vat']['name']} — standard {c['vat']['standard_rate']}, reduced {', '.join(c['vat']['reduced_rates'])}")

        lines.append("Business forms:")
        for bf in c['business_forms']:
            lines.append(f"  - {bf['local_name']}: {bf['description']}")

        lines.append(f"Social security: {c['social_security']}")
        lines.append(f"Immigration portal: {c['immigration_portal']['name']} — {c['immigration_portal']['url']}")
        if c.get('ukraine_support_portal'):
            lines.append(f"Ukraine support: {c['ukraine_support_portal']['name']} — {c['ukraine_support_portal']['url']}")

        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════
    # INTENT CLASSIFICATION
    # ══════════════════════════════════════════════════════════════

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
        "business_registration": ["business_registration", "government"],
        "subsidies":             ["subsidies"],
        "accounting":            ["accounting"],
        "reporting":             ["reporting_standards", "accounting"],
        "ukrainian_status":      ["ukrainian_status", "ukrainian_support", "immigration"],
        "ukrainian_business":    ["ukrainian_status", "business_registration", "immigration"],
        "double_taxation":       ["double_taxation", "tax"],
        "appointment":           ["company"],
        "greeting":              [],
        "other":                 [],
    }

    # ══════════════════════════════════════════════════════════════
    # MAIN PIPELINE
    # ══════════════════════════════════════════════════════════════

    async def process_message(
        self,
        message: str,
        session_id: str,
        channel: str = "web",
        mode: str = MODE_LIMITED,
    ) -> dict:
        language = self.detect_language(message)
        history = await self.redis.get_history(session_id)
        intent = await self.classify_intent(message)

        country = DEFAULT_COUNTRY
        if mode == MODE_FULL:
            country = await self.detect_country(message, session_id)

        categories = self.INTENT_TO_CATEGORIES.get(intent, [])
        context_chunks = []
        if categories:
            context_chunks = await self.qdrant.search(
                query=message, categories=categories, limit=5,
            )

        system_prompt = self._build_system_prompt(
            language, intent, context_chunks, mode, country,
        )

        messages = [{"role": "system", "content": system_prompt}]
        for h in history[-10:]:
            messages.append(h)
        messages.append({"role": "user", "content": message})

        resp = await self.client.chat.completions.create(
            model=self.model, messages=messages,
            max_tokens=1500 if mode == MODE_FULL else 1000,
            temperature=0.3,
        )
        assistant_message = resp.choices[0].message.content
        needs_escalation = self._check_escalation(intent, assistant_message, mode)

        await self.redis.add_to_history(session_id, "user", message)
        await self.redis.add_to_history(session_id, "assistant", assistant_message)

        logger.info("Message processed",
                    session_id=session_id, channel=channel,
                    language=language, intent=intent,
                    escalation=needs_escalation, mode=mode, country=country)

        return {
            "response": assistant_message,
            "language": language,
            "intent": intent,
            "needs_escalation": needs_escalation,
            "session_id": session_id,
            "country": country,
            "sources": [c.get("source_url", "") for c in context_chunks if c.get("source_url")],
        }

    # ══════════════════════════════════════════════════════════════
    # PROMPT BUILDER
    # ══════════════════════════════════════════════════════════════

    def _build_system_prompt(
        self, language: str, intent: str, chunks: list,
        mode: str = MODE_LIMITED, country: str = DEFAULT_COUNTRY,
    ) -> str:
        if mode == MODE_FULL:
            country_context = self._get_country_context(country)
            base_template = self.prompts.get(
                "base_system_prompt_full", self.prompts["base_system_prompt"],
            )
            base = base_template.replace("{country_context}", country_context)
            escalation = self.prompts.get(
                "escalation_prompt_full", self.prompts["escalation_prompt"],
            )
            anti_diy = self.prompts.get("anti_diy_prompt_full", "")
        else:
            base = self.prompts["base_system_prompt"]
            escalation = self.prompts["escalation_prompt"]
            anti_diy = self.prompts.get("anti_diy_prompt", "")

        formatting = self.prompts.get("formatting_prompt", "")

        context = ""
        if chunks:
            context = "\n\n## Relevant knowledge base information:\n\n"
            for i, c in enumerate(chunks, 1):
                context += (
                    f"### Source {i}: {c.get('source_name','')} "
                    f"[{c.get('country', '').upper()}]\n"
                    f"URL: {c.get('source_url','')}\n{c.get('text','')}\n\n"
                )

        lang_map = {
            "nl": "Antwoord in het Nederlands.",
            "en": "Respond in English.",
            "de": "Antworte auf Deutsch.",
            "fr": "Réponds en français.",
            "it": "Rispondi in italiano.",
            "es": "Responde en español.",
            "uk": "Відповідай українською мовою.",
            "ru": "Отвечай на русском языке.",
            "tr": "Türkçe yanıt ver.",
            "ar": "أجب باللغة العربية.",
            "pl": "Odpowiedz po polsku.",
            "ro": "Răspunde în limba română.",
            "zh": "请用中文回答。",
            "pt": "Responda em português.",
            "hr": "Odgovori na hrvatskom jeziku.",
            "sr": "Одговори на српском језику.",
            "bs": "Odgovori na bosanskom jeziku.",
            "bg": "Отговори на български език.",
            "sq": "Përgjigju në shqip.",
            "bn": "বাংলায় উত্তর দিন।",
            "ur": "اردو میں جواب دیں۔",
            "pa": "ਪੰਜਾਬੀ ਵਿੱਚ ਜਵਾਬ ਦਿਓ।",
            "hi": "हिंदी में उत्तर दें।",
            "tl": "Sumagot sa Tagalog.",
            "id": "Jawab dalam Bahasa Indonesia.",
        }

        parts = [base, escalation]
        if anti_diy:
            parts.append(anti_diy)
        parts.append(formatting)
        parts.append(lang_map.get(language, lang_map["en"]))
        if context:
            parts.append(context)

        return "\n\n".join(parts)

    # ══════════════════════════════════════════════════════════════
    # ESCALATION CHECK
    # ══════════════════════════════════════════════════════════════

    def _check_escalation(self, intent: str, response: str, mode: str = MODE_LIMITED) -> bool:
        if mode == MODE_FULL:
            if intent in {"appointment"}:
                return True
            markers = ["advocaat", "lawyer", "juridisch geschil", "fusie",
                        "overname", "strafrechtelijk", "Rechtsanwalt",
                        "avvocato", "avocat", "abogado"]
            return any(m in response.lower() for m in markers)
        else:
            if intent in {"appointment", "double_taxation", "ukrainian_business",
                           "tax_filing", "business_registration"}:
                return True
            markers = ["afspraak", "specialist", "консультац", "запис",
                        "appointment", "consultation"]
            return any(m in response.lower() for m in markers)
