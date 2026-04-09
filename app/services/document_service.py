"""Document & image processing service — OCR, translation, and analysis via GPT-4o Vision."""

import base64
import io
import json
import structlog
from pathlib import Path
from openai import AsyncOpenAI

from app.settings import get_settings

logger = structlog.get_logger()
settings = get_settings()

IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
DOCUMENT_TYPES = {".pdf"}
ALL_SUPPORTED = IMAGE_TYPES | DOCUMENT_TYPES
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".pdf": "application/pdf",
}

# ── LIMITED mode prompt ───────────────────────────────────────────
_PROMPT_LIMITED = """You are an AI assistant of TaxNavigator & Advice B.V.
The client uploaded a document or image.

## Tasks
1. **Identify** the document type (tax assessment, Belastingdienst letter, invoice, KVK extract, residence permit, payslip, toeslagen, etc.)
2. **Extract** key information — amounts, dates, reference numbers, deadlines
3. **Translate** the content if the document language differs from the client's language
4. **Explain** what the document means and what action may be needed
5. **Refer to TaxNavigator** when specific action is required

## Hard rules
- NEVER give specific tax calculations for the client's personal situation
- NEVER give step-by-step self-filing instructions or form-filling walkthroughs
- For complex documents always mention the option to book a consultation
- Protect privacy — refer to BSN/Steuer-ID/Codice Fiscale as "your tax ID (visible in the document)", never repeat it
- Respond in the CLIENT's language (see instruction below)

## FORMATTING — MANDATORY:
- NEVER use markdown headers (###, ####, #####).
- Use emoji at the start of each logical section.
- Use *bold* for important amounts, dates, and terms.
- Keep paragraphs short: 2-3 sentences max per block.

## Client's additional message
{user_message}"""

# ── FULL mode prompt ──────────────────────────────────────────────
_PROMPT_FULL = """You are a professional AI tax advisor of TaxNavigator & Advice B.V.
The client uploaded a document or image. You provide FULL professional analysis.

## Country Context
{country_context}

## Tasks
1. **Identify** the document type AND country of origin
2. **Extract ALL** information — every amount, date, reference number, deadline, tax ID (masked)
3. **Translate** the content if needed
4. **Detailed analysis** — explain what every section means using country-specific terminology
5. **Concrete action plan** — step-by-step what to do, using the correct portals for the country
6. **Calculate** any amounts due, potential deductions, or refunds
7. **Draft responses** if needed (e.g., objection letters)

## Rules
- Give SPECIFIC calculations and CONCRETE advice
- Use the correct terminology for the document's country
- Provide step-by-step portal instructions using country-specific portals
- Protect privacy — mask tax IDs
- Only recommend a human specialist for legal disputes or criminal matters
- Respond in the CLIENT's language

## FORMATTING — MANDATORY:
- NEVER use markdown headers (###, ####, #####).
- Use emoji at the start of each logical section.
- Use *bold* for important amounts, dates, and terms.
- Keep paragraphs short: 2-3 sentences max per block.

## Client's additional message
{user_message}"""

LANG_SUFFIX = {
    "nl": "\n\nAntwoord in het Nederlands.",
    "en": "\n\nRespond in English.",
    "de": "\n\nAntworte auf Deutsch.",
    "fr": "\n\nRéponds en français.",
    "it": "\n\nRispondi in italiano.",
    "es": "\n\nResponde en español.",
    "uk": "\n\nВідповідай українською мовою.",
    "ru": "\n\nОтвечай на русском языке.",
    "tr": "\n\nTürkçe yanıt ver.",
    "ar": "\n\nأجب باللغة العربية.",
    "pl": "\n\nOdpowiedz po polsku.",
    "ro": "\n\nRăspunde în limba română.",
    "zh": "\n\n请用中文回答。",
    "pt": "\n\nResponda em português.",
    "hr": "\n\nOdgovori na hrvatskom jeziku.",
    "sr": "\n\nОдговори на српском језику.",
    "bs": "\n\nOdgovori na bosanskom jeziku.",
    "bg": "\n\nОтговори на български език.",
    "sq": "\n\nPërgjigju në shqip.",
    "bn": "\n\nবাংলায় উত্তর দিন।",
    "ur": "\n\nاردو میں جواب دیں۔",
    "pa": "\n\nਪੰਜਾਬੀ ਵਿੱਚ ਜਵਾਬ ਦਿਓ।",
    "hi": "\n\nहिंदी में उत्तर दें।",
    "tl": "\n\nSumagot sa Tagalog.",
    "id": "\n\nJawab dalam Bahasa Indonesia.",
}


def _extract_pdf_text(file_data: bytes) -> str:
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(file_data))
        pages_text = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages_text.append(text.strip())
        return "\n\n".join(pages_text)
    except Exception as e:
        logger.warning("PDF text extraction failed", error=str(e))
        return ""


def _load_country_context(country: str) -> str:
    """Load country info from countries.json for document analysis."""
    try:
        with open("config/countries.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        info = data.get("countries", data).get(country)
        if not info:
            return f"Country: {country.upper()}"
        return (
            f"Country: {info.get('name_en', country.upper())} ({info.get('name_local', '')})\n"
            f"Tax authority: {info['tax_authority']['name']}\n"
            f"VAT: {info['vat']['name']} — {info['vat']['standard_rate']}\n"
            f"Income tax: {info['income_tax']['name']}"
        )
    except Exception:
        return f"Country: {country.upper()}"


class DocumentService:

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model

    def validate_file(self, filename: str, file_size: int) -> tuple[bool, str]:
        ext = Path(filename).suffix.lower()
        if ext not in ALL_SUPPORTED:
            return False, f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(ALL_SUPPORTED))}"
        if file_size > MAX_FILE_SIZE:
            return False, f"File too large ({file_size // 1024 // 1024}MB). Max 10 MB."
        if file_size == 0:
            return False, "File is empty"
        return True, ""

    async def analyze_document(
        self,
        file_data: bytes,
        filename: str,
        user_message: str = "",
        language: str = "nl",
        mode: str = "limited",
        country: str = "nl",
    ) -> dict:
        ext = Path(filename).suffix.lower()

        if mode == "full":
            country_ctx = _load_country_context(country)
            system = _PROMPT_FULL.format(
                user_message=user_message or "(no extra context)",
                country_context=country_ctx,
            )
        else:
            system = _PROMPT_LIMITED.format(
                user_message=user_message or "(no extra context)",
            )

        system += LANG_SUFFIX.get(language, LANG_SUFFIX["en"])

        if ext in IMAGE_TYPES:
            mime = MIME_MAP.get(ext, "image/jpeg")
            b64 = base64.b64encode(file_data).decode()
            content: list[dict] = [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"}},
                {"type": "text", "text": user_message or "Analyze this document."},
            ]
        else:
            pdf_text = _extract_pdf_text(file_data)
            if not pdf_text.strip():
                pdf_text = "[Could not extract text — the PDF may be scanned or image-based]"
            content = [{
                "type": "text",
                "text": (
                    f"The client uploaded a PDF file named '{filename}'.\n\n"
                    f"=== PDF CONTENT ===\n{pdf_text}\n=== END OF PDF ===\n\n"
                    f"Client's question: {user_message or 'Please analyze this document.'}"
                ),
            }]

        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
                max_tokens=2000 if mode == "full" else 1500,
                temperature=0.2,
            )
            analysis = resp.choices[0].message.content
            doc_type = self._detect_type(analysis)
            escalation = self._needs_escalation(doc_type, analysis, mode)

            logger.info("Document analyzed", filename=filename,
                       doc_type=doc_type, escalation=escalation,
                       mode=mode, country=country)
            return {"analysis": analysis, "document_type": doc_type, "needs_escalation": escalation}
        except Exception as e:
            logger.error("Document analysis failed", error=str(e), filename=filename)
            raise

    def _detect_type(self, text: str) -> str:
        t = text.lower()
        for dtype, kws in {
            "tax_assessment":   ["aanslag", "assessment", "steuerbescheid", "avviso", "liquidación",
                                  "оцінка", "уведомление"],
            "tax_letter":       ["brief van", "letter from", "schreiben vom", "lettera",
                                  "лист від", "письмо от", "belastingdienst", "finanzamt",
                                  "agenzia delle entrate", "agencia tributaria"],
            "invoice":          ["factuur", "invoice", "rechnung", "fattura", "factura",
                                  "рахунок", "счёт"],
            "kvk_extract":      ["kvk", "kamer van koophandel", "handelsregister",
                                  "camera di commercio", "registro mercantil"],
            "residence_permit": ["verblijf", "residence permit", "aufenthaltserlaubnis",
                                  "permesso di soggiorno", "residencia",
                                  "дозвіл на проживання", "вид на жительство"],
            "payslip":          ["loonstrook", "salary slip", "gehaltsabrechnung",
                                  "busta paga", "nómina", "зарплат"],
            "annual_statement": ["jaaropgave", "annual statement", "lohnsteuerbescheinigung",
                                  "certificazione unica", "certificado de retenciones"],
            "toeslagen":        ["toeslag", "benefit", "allowance", "kindergeld",
                                  "допомога", "пособие"],
        }.items():
            if any(k in t for k in kws):
                return dtype
        return "other"

    def _needs_escalation(self, doc_type: str, text: str, mode: str = "limited") -> bool:
        if mode == "full":
            markers = ["advocaat", "lawyer", "rechtsanwalt", "avvocato", "abogado",
                        "strafrechtelijk", "criminal", "rechtszaak", "court case"]
            return any(m in text.lower() for m in markers)
        else:
            if doc_type in ("tax_assessment", "tax_letter"):
                return True
            markers = ["bezwaar", "objection", "boete", "penalty", "штраф",
                        "naheffing", "specialist", "afspraak", "консультац"]
            return any(m in text.lower() for m in markers)
