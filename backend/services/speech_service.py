import os
import tempfile
from openai import AsyncOpenAI

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


async def transcribe_audio(audio_bytes: bytes, filename: str = "audio.webm") -> str:
    with tempfile.NamedTemporaryFile(suffix=os.path.splitext(filename)[1], delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as f:
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=(filename, f, "audio/webm"),
            )
        return transcript.text
    finally:
        os.unlink(tmp_path)


async def synthesize_speech(text: str) -> bytes:
    response = await client.audio.speech.create(
        model="tts-1",
        voice="nova",
        input=text,
        response_format="mp3",
    )
    return response.content
