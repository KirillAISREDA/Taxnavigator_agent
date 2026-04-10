"""Audio transcription service using OpenAI Whisper API."""

import io
import structlog
from openai import AsyncOpenAI

from app.settings import get_settings

logger = structlog.get_logger()
settings = get_settings()


async def transcribe_audio(file_data: bytes, filename: str) -> str:
    """Transcribe audio using OpenAI Whisper API.

    Args:
        file_data: Raw audio bytes (ogg, webm, mp3, wav, etc.)
        filename: Original filename with extension.

    Returns:
        Transcribed text string.
    """
    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)

    audio_file = io.BytesIO(file_data)
    audio_file.name = filename or "voice.ogg"

    transcript = await openai_client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
        response_format="text",
    )

    text = transcript.strip() if isinstance(transcript, str) else transcript.text.strip()
    logger.info("Voice transcribed", length=len(text), preview=text[:80])
    return text
