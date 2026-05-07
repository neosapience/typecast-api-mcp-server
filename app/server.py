import base64
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
    volume: int | None = Field(
        default=None,
        description="Audio volume level (0-200). When omitted, the server applies its default. Must NOT be sent together with target_lufs — the API rejects any presence of volume alongside target_lufs.",
        ge=0,
        le=200,
    )
    audio_pitch: int = Field(default=0, description="Audio pitch adjustment", ge=-12, le=12)
    audio_tempo: float = Field(default=1.0, description="Audio playback speed", ge=0.5, le=2.0)
    audio_format: str = Field(default="wav", pattern="^(wav|mp3)$", description="Audio file format")
    target_lufs: float | None = Field(
        default=None,
        description="Absolute loudness normalization target in LUFS. Mutually exclusive with volume on the non-streaming endpoint (any presence of volume causes 4xx); not accepted by the streaming endpoint at all.",
        ge=-70.0,
        le=0.0,
    )


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
    use_cases: str | None = None,
) -> dict:
    """Get a list of available voices for text-to-speech using V2 API

    Args:
        model: Optional filter for specific TTS models (ssfm-v21 or ssfm-v30).
        gender: Optional filter for voice gender (male or female).
        age: Optional filter for voice age group (child, teen, young_adult, middle_aged, senior).
        use_cases: Optional filter for voice use case (e.g. 'audiobook', 'narration', 'documentary').
            Pass a single use case string supported by the V2 voices endpoint.

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
    if use_cases:
        params["use_cases"] = use_cases

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
    target_lufs: float | None = None,
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
        target_lufs: Optional absolute loudness normalization target in LUFS (-70.0 ~ 0.0).
            Mutually exclusive with a custom volume value on this non-streaming endpoint.

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

    if target_lufs is not None and volume != 100:
        raise ValueError(
            "target_lufs is mutually exclusive with a custom volume; "
            "leave volume at the default (100) or unset target_lufs."
        )
    output_kwargs: dict = {
        "audio_pitch": audio_pitch,
        "audio_tempo": audio_tempo,
        "audio_format": audio_format,
    }
    if target_lufs is not None:
        output_kwargs["target_lufs"] = target_lufs
    else:
        output_kwargs["volume"] = volume
    output_model = Output(**output_kwargs)
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


@app.tool(
    "text_to_speech_stream",
    "Convert text to speech with chunked streaming for low-latency delivery",
)
async def text_to_speech_stream(
    voice_id: str,
    text: str,
    model: str = TTSModel.SSFM_V30.value,
    emotion_type: str = "preset",
    emotion_preset: str = EmotionEnum.NORMAL.value,
    emotion_intensity: float = 1.0,
    previous_text: str | None = None,
    next_text: str | None = None,
    audio_pitch: int = 0,
    audio_tempo: float = 1.0,
    audio_format: str = "wav",
) -> str:
    """Convert text to speech via the streaming endpoint and save the result.

    Calls POST /v1/text-to-speech/stream which returns chunked audio data
    in real time. The chunks are concatenated and saved as a single file.

    Note: the streaming endpoint does not accept volume or target_lufs (the
    server rejects those fields). Use text_to_speech for full output controls.

    Args:
        voice_id: ID of the voice to use
        text: Text to convert to speech
        model: TTS model (ssfm-v21 or ssfm-v30, default: ssfm-v30)
        emotion_type: For ssfm-v30: 'preset' or 'smart' (default: preset)
        emotion_preset: Emotion preset name (default: normal)
        emotion_intensity: Emotion intensity, 0.0 ~ 2.0 (default: 1.0)
        previous_text: For smart mode - previous context text
        next_text: For smart mode - next context text
        audio_pitch: -12 ~ 12 (default: 0)
        audio_tempo: 0.5 ~ 2.0 (default: 1.0)
        audio_format: 'wav' or 'mp3' (default: wav)

    Returns:
        Path to the saved audio file
    """
    if not OUTPUT_DIR.exists():
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    model_enum = TTSModel(model)
    if model_enum == TTSModel.SSFM_V30:
        if emotion_type == "smart":
            prompt_payload = SmartPrompt(
                emotion_type=EmotionType.SMART,
                previous_text=previous_text,
                next_text=next_text,
            ).model_dump(exclude_none=True)
        else:
            prompt_payload = PresetPrompt(
                emotion_type=EmotionType.PRESET,
                emotion_preset=emotion_preset,
                emotion_intensity=emotion_intensity,
            ).model_dump(exclude_none=True)
    else:
        prompt_payload = Prompt(
            emotion_preset=emotion_preset,
            emotion_intensity=emotion_intensity,
        ).model_dump(exclude_none=True)

    output_payload = {
        "audio_pitch": audio_pitch,
        "audio_tempo": audio_tempo,
        "audio_format": audio_format,
    }

    request_payload = {
        "voice_id": voice_id,
        "text": text,
        "model": model,
        "prompt": prompt_payload,
        "output": output_payload,
    }

    safe_text = re.sub(r"\s+", "", text[:10])
    output_path = (
        OUTPUT_DIR
        / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{voice_id}_{safe_text}_stream.{audio_format}"
    )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, write=30.0, read=None, pool=10.0)
    ) as client:
        async with client.stream(
            "POST",
            f"{API_HOST}/v1/text-to-speech/stream",
            json=request_payload,
            headers=HTTP_HEADERS,
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise Exception(
                    f"Failed to stream speech: {response.status_code}, {body.decode(errors='ignore')}"
                )

            with output_path.open("wb") as f:
                async for chunk in response.aiter_bytes():
                    if chunk:
                        f.write(chunk)

    return str(output_path)


@app.tool(
    "get_my_subscription",
    "Get the authenticated user's subscription plan, credit usage, and concurrency limit",
)
async def get_my_subscription() -> dict:
    """Get the authenticated user's subscription information.

    Calls GET /v1/users/me/subscription and returns the plan tier, credits
    (used / total), and concurrency limit.

    Returns:
        Dict with this shape:
            {
                "plan": "free" | "lite" | "plus" | "custom",
                "credits": {"plan_credits": int, "used_credits": int},
                "limits": {"concurrency_limit": int}
            }
    """
    url = f"{API_HOST}/v1/users/me/subscription"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=HTTP_HEADERS)
        if response.status_code != 200:
            raise Exception(
                f"Failed to get subscription: {response.status_code}, {response.text}"
            )
        return response.json()


@app.tool(
    "text_to_speech_with_timestamps",
    "Convert text to speech with word- or character-level timestamp alignment for caption generation",
)
async def text_to_speech_with_timestamps(
    voice_id: str,
    text: str,
    model: str = TTSModel.SSFM_V30.value,
    granularity: str | None = None,
    emotion_type: str = "preset",
    emotion_preset: str = EmotionEnum.NORMAL.value,
    emotion_intensity: float = 1.0,
    previous_text: str | None = None,
    next_text: str | None = None,
    language: str | None = None,
    volume: int = 100,
    audio_pitch: int = 0,
    audio_tempo: float = 1.0,
    audio_format: str = "wav",
) -> dict:
    """Convert text to speech and return timestamp alignment for caption generation.

    Calls POST /v1/text-to-speech/with-timestamps. Saves the audio file and
    returns the file path together with the raw alignment payload (words and
    characters arrays as returned by the server).

    For non-whitespace languages such as jpn or zho, pass granularity='char'
    or 'both'. With 'word' on those languages the server collapses the entire
    sentence into a single word segment.

    Args:
        voice_id: ID of the voice to use
        text: Text to convert to speech
        model: TTS model (default: ssfm-v30)
        granularity: 'word', 'char', or 'both'. None lets the server use its
            default (word). For jpn/zho prefer 'char' or 'both'.
        emotion_type, emotion_preset, emotion_intensity, previous_text,
        next_text, language, volume, audio_pitch, audio_tempo, audio_format:
            same shape as text_to_speech.

    Returns:
        Dict:
            - 'audio_path': str — path to the saved audio file
            - 'words': list | None — word-level alignment when available
            - 'characters': list | None — character-level alignment when available
            - 'raw': dict — full server response with the audio bytes stripped
    """
    if not OUTPUT_DIR.exists():
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    model_enum = TTSModel(model)
    if model_enum == TTSModel.SSFM_V30:
        if emotion_type == "smart":
            prompt_payload = SmartPrompt(
                emotion_type=EmotionType.SMART,
                previous_text=previous_text,
                next_text=next_text,
            ).model_dump(exclude_none=True)
        else:
            prompt_payload = PresetPrompt(
                emotion_type=EmotionType.PRESET,
                emotion_preset=emotion_preset,
                emotion_intensity=emotion_intensity,
            ).model_dump(exclude_none=True)
    else:
        prompt_payload = Prompt(
            emotion_preset=emotion_preset,
            emotion_intensity=emotion_intensity,
        ).model_dump(exclude_none=True)

    output_payload = Output(
        volume=volume,
        audio_pitch=audio_pitch,
        audio_tempo=audio_tempo,
        audio_format=audio_format,
    ).model_dump(exclude_none=True)

    request_payload: dict = {
        "voice_id": voice_id,
        "text": text,
        "model": model,
        "prompt": prompt_payload,
        "output": output_payload,
    }
    if language:
        request_payload["language"] = language
    if granularity:
        request_payload["granularity"] = granularity

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, write=30.0, read=120.0, pool=10.0)
    ) as client:
        response = await client.post(
            f"{API_HOST}/v1/text-to-speech/with-timestamps",
            json=request_payload,
            headers=HTTP_HEADERS,
        )
        if response.status_code != 200:
            raise Exception(
                f"Failed to generate timestamped speech: {response.status_code}, {response.text}"
            )

    payload = response.json()

    audio_b64 = payload.get("audio", "")
    audio_bytes = base64.b64decode(audio_b64) if audio_b64 else b""

    safe_text = re.sub(r"\s+", "", text[:10])
    audio_path = (
        OUTPUT_DIR
        / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{voice_id}_{safe_text}_ts.{audio_format}"
    )
    audio_path.write_bytes(audio_bytes)

    # Server returns words / characters at the top level of the response,
    # matching typecast-go/timestamps.go (TTSWithTimestampsResponse). There
    # is no `alignment` wrapper.
    words = payload.get("words")
    characters = payload.get("characters")

    raw = {k: v for k, v in payload.items() if k != "audio"}

    return {
        "audio_path": str(audio_path),
        "words": words,
        "characters": characters,
        "raw": raw,
    }
