# app/utils/tts.py
import os
import hashlib
from openai import OpenAI
import runtime_settings as rt

client = OpenAI(api_key=rt.OPENAI_API_KEY)

# portable audio directory (works on Windows, macOS, Linux)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(BASE_DIR, "..", "audio_responses")
AUDIO_DIR = os.path.abspath(AUDIO_DIR)
os.makedirs(AUDIO_DIR, exist_ok=True)

# "alloy", "lively", "soft", "calm", "verse"
VOICE = "alloy"

def synthesize_speech(text: str, filename: str | None = None) -> str:
    """Generate or reuse TTS audio for the given text."""
    if not text.strip():
        text = "I'm sorry, I didn't catch that."

    if not filename:
        filename = hashlib.md5(text.encode()).hexdigest()

    path = os.path.join(AUDIO_DIR, f"{filename}.mp3")

    # reuse cached audio
    if os.path.exists(path):
        return path

    print(f"[TTS] Synthesizing with voice '{VOICE}' → {path}")

    with client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice=VOICE,
        input=text
    ) as response:
        response.stream_to_file(path)

    return path
