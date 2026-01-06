import os
import re
from datetime import datetime
from enum import Enum
from pathlib import Path

import httpx
import sounddevice as sd
import soundfile as sf
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from app.knowledge import TYPECAST_API_KNOWLEDGE

API_HOST = os.environ.get("TYPECAST_API_HOST", "https://api.typecast.ai")
API_KEY = os.environ.get("TYPECAST_API_KEY")
OUTPUT_DIR = Path(os.environ.get("TYPECAST_OUTPUT_DIR", os.path.expanduser("~/Downloads/typecast_output")))
HTTP_HEADERS = { "X-API-KEY": API_KEY }

app = FastMCP(
    "typecast-api-mcp-server",
    instructions=TYPECAST_API_KNOWLEDGE,
    host="0.0.0.0",
    port=8000,
)


class TTSModel(str, Enum):
    SSFM_V21 = "ssfm-v21"


class EmotionEnum(str, Enum):
    """Emotion presets supported by the Typecast TTS API.
    
    Note: Each voice may only support a subset of these emotions.
    Check the 'emotions' field in the voice response from GET /v1/voices.
    """
    NORMAL = "normal"
    SAD = "sad"
    HAPPY = "happy"
    ANGRY = "angry"


class Prompt(BaseModel):
    emotion_preset: EmotionEnum = Field(default=EmotionEnum.NORMAL, description="Emotion preset type")
    emotion_intensity: float = Field(default=1.0, description="Intensity of the emotion", ge=0.0, le=2.0)


class Output(BaseModel):
    volume: int = Field(default=100, description="Audio volume level", ge=0, le=200)
    audio_pitch: int = Field(default=0, description="Audio pitch adjustment", ge=-12, le=12)
    audio_tempo: float = Field(default=1.0, description="Audio playback speed", ge=0.5, le=2.0)
    audio_format: str = Field(default="wav", pattern="^(wav|mp3)$", description="Audio file format")


class Voice(BaseModel):
    voice_id: str = Field(description="Unique voice identifier")
    voice_name: str = Field(description="Display name of the voice")
    model: TTSModel = Field(description="TTS model type")
    emotions: list[EmotionEnum] = Field(description="List of supported emotions")


class TTSRequest(BaseModel):
    voice_id: str = Field(description="Voice identifier to use")
    text: str = Field(description="Text to convert to speech")
    model: TTSModel = Field(description="TTS model to use")
    language: str | None = Field(default=None, description="Language code based on ISO 639-3")
    prompt: Prompt | None = Field(default_factory=Prompt, description="Prompt configuration for speech generation")
    output: Output | None = Field(default_factory=Output, description="Output audio configuration")
    seed: int | None = Field(default=None, description="Random seed for consistent generation", ge=0, le=2147483647)


@app.tool("get_voices", "Get a list of available voices for text-to-speech")
async def get_voices(model: str = TTSModel.SSFM_V21.value) -> dict:
    """Get a list of available voices for text-to-speech

    Args:
        model: Optional filter for specific TTS models.

    Returns:
        List of available voices.
    """
    model = TTSModel(model)

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{API_HOST}/v1/voices?model={model.value}",
            headers=HTTP_HEADERS,
        )
        if response.status_code != 200:
            raise Exception(f"Failed to get voices: {response.status_code}")
        return response.json()


@app.tool("text_to_speech", "Convert text to speech using the specified voice and parameters")
async def text_to_speech(
    voice_id: str,
    text: str,
    model: str,
    emotion_preset: str = EmotionEnum.NORMAL.value,
    emotion_intensity: float = 1.0,
    volume: int = 100,
    audio_pitch: int = 0,
    audio_tempo: float = 1.0,
    audio_format: str = "wav",
) -> str:
    """Convert text to speech using the specified voice and parameters

    Args:
        voice_id: ID of the voice to use
        text: Text to convert to speech
        model: TTS model to use
        emotion_preset: Emotion preset type (default: normal)
        emotion_intensity: Intensity of the emotion, between 0.0 and 2.0 (default: 1.0)
        volume: Audio volume level, between 0 and 200 (default: 100)
        audio_pitch: Audio pitch adjustment, between -12 and 12 (default: 0)
        audio_tempo: Audio playback speed, between 0.5 and 2.0 (default: 1.0)
        audio_format: Audio format, either 'wav' or 'mp3' (default: wav)

    Returns:
        Path to the saved audio file
    """
    if not OUTPUT_DIR.exists():
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Pydantic 모델을 사용하여 자동 검증
    prompt_model = Prompt(emotion_preset=emotion_preset, emotion_intensity=emotion_intensity)
    output_model = Output(volume=volume, audio_pitch=audio_pitch, audio_tempo=audio_tempo, audio_format=audio_format)
    request = TTSRequest(voice_id=voice_id, text=text, model=model, prompt=prompt_model, output=output_model)  # TTSModel 검증은 Pydantic이 자동으로 처리

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_HOST}/v1/text-to-speech",
            json=request.model_dump(exclude_none=True),
            headers=HTTP_HEADERS,
        )
        if response.status_code != 200:
            raise Exception(f"Failed to generate speech: {response.status_code}, {response.text}")

        safe_text = re.sub(r'\s+', '', text[:10])
        output_path = OUTPUT_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{voice_id}_{safe_text}.wav"
        output_path.write_bytes(response.content)

        return str(output_path)


@app.tool("play_audio", "Play the generated audio file")
async def play_audio(file_path: str) -> str:
    """Play the audio file at the specified path

    Args:
        file_path: Path to the audio file to play

    Returns:
        Status message
    """
    try:
        data, samplerate = sf.read(file_path)

        # Get the current output device
        output_device = sd.default.device[1]  # [input, output]

        # Play on the current output device
        sd.play(data, samplerate, device=output_device)
        sd.wait()

        return f"Successfully played audio file: {file_path}"
    except Exception as e:
        return f"Failed to play audio file: {str(e)}"
