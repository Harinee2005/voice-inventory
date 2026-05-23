import logging
import os
import tempfile
import time

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
    return _client


async def transcribe_audio(audio_bytes: bytes, filename: str = "audio.webm") -> str:
    logger.info("TRANSCRIBE ──▶  filename=%r  bytes=%d", filename, len(audio_bytes))
    t0 = time.perf_counter()
    with tempfile.NamedTemporaryFile(suffix=os.path.splitext(filename)[1], delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as f:
            transcript = await _get_client().audio.transcriptions.create(
                model="whisper-1",
                file=(filename, f, "audio/webm"),
            )
        elapsed = (time.perf_counter() - t0) * 1000
        logger.info("TRANSCRIBE ◀──  elapsed=%.0fms  transcript=%r", elapsed, transcript.text[:200])
        return transcript.text
    except Exception as exc:
        logger.error("TRANSCRIBE ERROR  elapsed=%.0fms  error=%s", (time.perf_counter() - t0) * 1000, exc)
        raise
    finally:
        os.unlink(tmp_path)


async def synthesize_speech(text: str) -> bytes:
    logger.info("TTS ──▶  text_len=%d  text=%r", len(text), text[:100])
    t0 = time.perf_counter()
    try:
        response = await _get_client().audio.speech.create(
            model="tts-1",
            voice="nova",
            input=text,
            response_format="mp3",
        )
        elapsed = (time.perf_counter() - t0) * 1000
        logger.info("TTS ◀──  elapsed=%.0fms  output_bytes=%d", elapsed, len(response.content))
        return response.content
    except Exception as exc:
        logger.error("TTS ERROR  elapsed=%.0fms  error=%s", (time.perf_counter() - t0) * 1000, exc)
        raise
