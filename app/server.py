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
    SSFM_V30 = "ssfm-v30"


class EmotionEnum(str, Enum):
    """Emotion presets supported by the Typecast TTS API.

    Note: ssfm-v21 supports: normal, happy, sad, angry
    Note: ssfm-v30 supports: normal, happy, sad, angry, whisper, toneup, tonedown
    """
    NORMAL = "normal"
    SAD = "sad"
    HAPPY = "happy"
    ANGRY = "angry"
    WHISPER = "whisper"      # ssfm-v30 only
    TONEUP = "toneup"        # ssfm-v30 only
    TONEDOWN = "tonedown"    # ssfm-v30 only


class EmotionType(str, Enum):
    """Emotion type for ssfm-v30 model."""
    PRESET = "preset"
    SMART = "smart"


class Prompt(BaseModel):
    """Basic prompt for ssfm-v21 model."""
    emotion_preset: EmotionEnum = Field(default=EmotionEnum.NORMAL, description="Emotion preset type")
    emotion_intensity: float = Field(default=1.0, description="Intensity of the emotion", ge=0.0, le=2.0)


class PresetPrompt(BaseModel):
    """Preset-based emotion control for ssfm-v30 model."""
    emotion_type: EmotionType = Field(default=EmotionType.PRESET, description="Must be 'preset' for preset mode")
    emotion_preset: EmotionEnum = Field(default=EmotionEnum.NORMAL, description="Emotion preset: normal, happy, sad, angry, whisper, toneup, tonedown")
    emotion_intensity: float = Field(default=1.0, description="Intensity of the emotion", ge=0.0, le=2.0)


class SmartPrompt(BaseModel):
    """Context-aware emotion inference for ssfm-v30 model."""
    emotion_type: EmotionType = Field(default=EmotionType.SMART, description="Must be 'smart' for smart mode")
    previous_text: str | None = Field(default=None, description="Previous context text for emotion inference")
    next_text: str | None = Field(default=None, description="Next context text for emotion inference")


class Output(BaseModel):
    volume: int = Field(default=100, description="Audio volume level", ge=0, le=200)
    audio_pitch: int = Field(default=0, description="Audio pitch adjustment", ge=-12, le=12)
    audio_tempo: float = Field(default=1.0, description="Audio playback speed", ge=0.5, le=2.0)
    audio_format: str = Field(default="wav", pattern="^(wav|mp3)$", description="Audio file format")


class GenderEnum(str, Enum):
    """Gender filter for V2 Voices API."""
    MALE = "male"
    FEMALE = "female"


class AgeEnum(str, Enum):
    """Age filter for V2 Voices API."""
    CHILD = "child"
    TEEN = "teen"
    YOUNG_ADULT = "young_adult"
    MIDDLE_AGED = "middle_aged"
    SENIOR = "senior"


class VoiceModel(BaseModel):
    """Voice model information in V2 API response."""
    version: TTSModel = Field(description="Model version")
    emotions: list[str] = Field(description="List of supported emotions for this model")


class VoiceV2(BaseModel):
    """V2 Voice response with enhanced metadata."""
    voice_id: str = Field(description="Unique voice identifier")
    voice_name: str = Field(description="Display name of the voice")
    models: list[VoiceModel] = Field(description="List of supported models with their emotions")
    gender: GenderEnum | None = Field(default=None, description="Voice gender")
    age: AgeEnum | None = Field(default=None, description="Voice age group")
    use_cases: list[str] | None = Field(default=None, description="Recommended use cases")


class TTSRequest(BaseModel):
    voice_id: str = Field(description="Voice identifier to use")
    text: str = Field(description="Text to convert to speech")
    model: TTSModel = Field(description="TTS model to use")
    language: str | None = Field(default=None, description="Language code based on ISO 639-3")
    prompt: Prompt | PresetPrompt | SmartPrompt | None = Field(default=None, description="Prompt configuration for speech generation")
    output: Output | None = Field(default_factory=Output, description="Output audio configuration")
    seed: int | None = Field(default=None, description="Random seed for consistent generation", ge=0, le=2147483647)


@app.tool("get_voices", "Get a list of available voices using V2 API with filtering support")
async def get_voices(
    model: str | None = None,
    gender: str | None = None,
    age: str | None = None,
) -> dict:
    """Get a list of available voices for text-to-speech using V2 API

    Args:
        model: Optional filter for specific TTS models (ssfm-v21 or ssfm-v30).
        gender: Optional filter for voice gender (male or female).
        age: Optional filter for voice age group (child, teen, young_adult, middle_aged, senior).

    Returns:
        List of available voices with enhanced metadata including gender, age, and use cases.
    """
    params = {}
    if model:
        params["model"] = TTSModel(model).value
    if gender:
        params["gender"] = GenderEnum(gender).value
    if age:
        params["age"] = AgeEnum(age).value

    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{API_HOST}/v2/voices"
    if query_string:
        url = f"{url}?{query_string}"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=HTTP_HEADERS)
        if response.status_code != 200:
            raise Exception(f"Failed to get voices: {response.status_code}")
        return response.json()


@app.tool("get_voice", "Get detailed information for a specific voice by ID using V2 API")
async def get_voice(voice_id: str) -> dict:
    """Get detailed information for a specific voice by ID using V2 API

    Args:
        voice_id: The voice ID (e.g., 'tc_672c5f5ce59fac2a48faeaee')

    Returns:
        Voice information with enhanced metadata including gender, age, use cases, and supported models with emotions.
    """
    url = f"{API_HOST}/v2/voices/{voice_id}"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=HTTP_HEADERS)
        if response.status_code != 200:
            raise Exception(f"Failed to get voice: {response.status_code}")
        return response.json()


@app.tool("text_to_speech", "Convert text to speech using the specified voice and parameters")
async def text_to_speech(
    voice_id: str,
    text: str,
    model: str = TTSModel.SSFM_V30.value,
    emotion_type: str = "preset",
    emotion_preset: str = EmotionEnum.NORMAL.value,
    emotion_intensity: float = 1.0,
    previous_text: str | None = None,
    next_text: str | None = None,
    volume: int = 100,
    audio_pitch: int = 0,
    audio_tempo: float = 1.0,
    audio_format: str = "wav",
) -> str:
    """Convert text to speech using the specified voice and parameters

    Args:
        voice_id: ID of the voice to use
        text: Text to convert to speech
        model: TTS model to use (ssfm-v21 or ssfm-v30, default: ssfm-v30)
        emotion_type: For ssfm-v30: 'preset' for explicit emotion or 'smart' for context-aware inference (default: preset)
        emotion_preset: Emotion preset type. v21: normal/happy/sad/angry. v30: adds whisper/toneup/tonedown (default: normal)
        emotion_intensity: Intensity of the emotion, between 0.0 and 2.0 (default: 1.0)
        previous_text: For smart mode - previous context text for emotion inference
        next_text: For smart mode - next context text for emotion inference
        volume: Audio volume level, between 0 and 200 (default: 100)
        audio_pitch: Audio pitch adjustment, between -12 and 12 (default: 0)
        audio_tempo: Audio playback speed, between 0.5 and 2.0 (default: 1.0)
        audio_format: Audio format, either 'wav' or 'mp3' (default: wav)

    Returns:
        Path to the saved audio file
    """
    if not OUTPUT_DIR.exists():
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Build prompt based on model and emotion_type
    model_enum = TTSModel(model)
    if model_enum == TTSModel.SSFM_V30:
        if emotion_type == "smart":
            prompt_model = SmartPrompt(
                emotion_type=EmotionType.SMART,
                previous_text=previous_text,
                next_text=next_text
            )
        else:
            prompt_model = PresetPrompt(
                emotion_type=EmotionType.PRESET,
                emotion_preset=emotion_preset,
                emotion_intensity=emotion_intensity
            )
    else:
        # ssfm-v21 uses basic Prompt
        prompt_model = Prompt(emotion_preset=emotion_preset, emotion_intensity=emotion_intensity)

    output_model = Output(volume=volume, audio_pitch=audio_pitch, audio_tempo=audio_tempo, audio_format=audio_format)
    request = TTSRequest(voice_id=voice_id, text=text, model=model, prompt=prompt_model, output=output_model)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_HOST}/v1/text-to-speech",
            json=request.model_dump(exclude_none=True),
            headers=HTTP_HEADERS,
        )
        if response.status_code != 200:
            raise Exception(f"Failed to generate speech: {response.status_code}, {response.text}")

        safe_text = re.sub(r'\s+', '', text[:10])
        output_path = OUTPUT_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{voice_id}_{safe_text}.{audio_format}"
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
