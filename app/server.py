import base64
import mimetypes
import os
import platform
import re
import secrets
import time
from contextvars import ContextVar
from datetime import datetime
from enum import Enum
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import anyio
import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from app.knowledge import TYPECAST_API_KNOWLEDGE

API_HOST = os.environ.get("TYPECAST_API_HOST", "https://api.typecast.ai")
API_KEY = os.environ.get("TYPECAST_API_KEY")
OUTPUT_DIR = Path(os.environ.get("TYPECAST_OUTPUT_DIR", os.path.expanduser("~/Downloads/typecast_output")))
DOCS_SERVICE_URL = os.environ.get("DOCS_SERVICE_URL", "https://typecast.ai/docs").rstrip("/")
PUBLIC_FILE_URL = os.environ.get("PUBLIC_FILE_URL", "https://typecast.ai/docs/mcp/files").rstrip("/")
REMOTE_MODE = os.environ.get("MCP_REMOTE_MODE", "").lower() in {"1", "true", "yes"}
REMOTE_FILE_TTL_SECONDS = int(os.environ.get("MCP_FILE_TTL_SECONDS", "3600"))
QUICK_CLONING_MAX_FILE_SIZE = 25 * 1024 * 1024
PUBLIC_TOOLS = {"search_documentation"}
_request_api_key: ContextVar[str | None] = ContextVar("request_api_key", default=None)
_request_attribution: ContextVar[tuple[str | None, str | None] | None] = ContextVar(
    "request_attribution", default=None
)
MCP_VERSION = version("typecast-api-mcp-server")
_ATTRIBUTION_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")


def _user_agent() -> str:
    deployment = "hosted" if REMOTE_MODE else "self-hosted"
    user_agent = (
        f"typecast-mcp/{MCP_VERSION} Python/{platform.python_version()} "
        f"httpx/{httpx.__version__} (deployment={deployment})"
    )

    request_attribution = _request_attribution.get()
    if request_attribution is None:
        source = os.environ.get("TYPECAST_INTEGRATION_SOURCE") or None
        generated_by = os.environ.get("TYPECAST_GENERATED_BY") or None
    else:
        source, generated_by = request_attribution

    if source is None and generated_by is None:
        return user_agent
    if source is None or generated_by is None:
        raise ToolError("TYPECAST_INTEGRATION_SOURCE and TYPECAST_GENERATED_BY must be set together.")
    if source not in {"llms", "skill", "api-page", "api-docs"}:
        raise ToolError(
            "Typecast integration source must be 'llms', 'skill', 'api-page', or 'api-docs'."
        )
    if not _ATTRIBUTION_TOKEN.fullmatch(generated_by):
        raise ToolError("Typecast generated_by must be a lowercase ASCII token of 1-32 characters.")
    return (
        f"{user_agent} typecast-integration/1 "
        f"(source={source}; generated_by={generated_by})"
    )


def _api_headers() -> dict[str, str]:
    api_key = _request_api_key.get() or (None if REMOTE_MODE else API_KEY)
    if not api_key:
        raise ToolError("Authentication required. Send your Typecast API key as X-API-KEY or Bearer token.")
    return {"X-API-KEY": api_key, "User-Agent": _user_agent()}


class TypecastMCP(FastMCP):
    async def list_tools(self):
        tools = await super().list_tools()
        if not REMOTE_MODE:
            return tools
        if not _request_api_key.get():
            return [tool for tool in tools if tool.name in PUBLIC_TOOLS]
        return [tool for tool in tools if tool.name != "play_audio"]

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        if REMOTE_MODE and name not in PUBLIC_TOOLS and not _request_api_key.get():
            raise ToolError("Authentication required. Send your Typecast API key as X-API-KEY or Bearer token.")
        if REMOTE_MODE and name == "play_audio":
            raise ToolError("play_audio is available only when the MCP server runs on your local computer.")
        return await super().call_tool(name, arguments)


class ApiKeyMiddleware:
    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        api_key = None
        attribution = None
        if scope["type"] == "http":
            headers = {name.lower(): value for name, value in scope.get("headers", [])}
            api_key = headers.get(b"x-api-key")
            authorization = headers.get(b"authorization", b"")
            if not api_key and authorization.lower().startswith(b"bearer "):
                api_key = authorization[7:]
            if api_key:
                api_key = api_key.decode("utf-8", errors="ignore")[:512]
            source = headers.get(b"x-typecast-integration-source")
            generated_by = headers.get(b"x-typecast-generated-by")
            if source is not None or generated_by is not None:
                attribution = (
                    source[:128].decode("ascii", errors="replace") if source else None,
                    generated_by[:128].decode("ascii", errors="replace") if generated_by else None,
                )
        api_key_token = _request_api_key.set(api_key)
        attribution_token = _request_attribution.set(attribution)
        try:
            await self.asgi_app(scope, receive, send)
        finally:
            _request_attribution.reset(attribution_token)
            _request_api_key.reset(api_key_token)


def _sanitize_for_filename(s: str) -> str:
    """Strip path separators and other unsafe characters for filename use.

    Defends against a caller passing voice_id (or any other interpolated
    component) that contains '/', '..', or control characters, which would
    otherwise let the resulting OUTPUT_DIR path escape the configured
    output directory.
    """
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s)


def _validate_quick_clone_audio_path(audio_file_path: str) -> tuple[Path, str, int]:
    audio_path = Path(audio_file_path).expanduser()
    if not audio_path.exists() or not audio_path.is_file():
        raise ValueError(f"Audio file does not exist: {audio_file_path}")

    file_size = audio_path.stat().st_size
    if file_size > QUICK_CLONING_MAX_FILE_SIZE:
        raise ValueError(
            f"Audio file exceeds the 25 MB quick cloning limit; got {file_size} bytes."
        )

    content_type = mimetypes.guess_type(audio_path.name)[0]
    if content_type == "audio/x-wav":
        content_type = "audio/wav"
    if content_type not in {"audio/wav", "audio/mpeg"}:
        suffix = audio_path.suffix.lower()
        if suffix == ".wav":
            content_type = "audio/wav"
        elif suffix == ".mp3":
            content_type = "audio/mpeg"
        else:
            raise ValueError("Quick cloning accepts WAV or MP3 audio only.")

    return audio_path, content_type, file_size


def _quick_clone_audio(
    audio_file_path: str | None,
    audio_base64: str | None,
    audio_filename: str,
) -> tuple[str, bytes, str, int]:
    if audio_base64:
        try:
            content = base64.b64decode(audio_base64, validate=True)
        except ValueError as error:
            raise ValueError("audio_base64 must contain valid base64 data") from error
        if len(content) > QUICK_CLONING_MAX_FILE_SIZE:
            raise ValueError("Audio file exceeds the 25 MB quick cloning limit.")
        suffix = Path(audio_filename).suffix.lower()
        if suffix not in {".wav", ".mp3"}:
            raise ValueError("audio_filename must end in .wav or .mp3")
        return Path(audio_filename).name, content, "audio/wav" if suffix == ".wav" else "audio/mpeg", len(content)
    if not audio_file_path:
        raise ValueError("Provide audio_file_path locally or audio_base64 when using the remote server.")
    if REMOTE_MODE:
        raise ValueError("audio_file_path is not supported remotely; send audio_base64 and audio_filename.")
    audio_path, content_type, file_size = _validate_quick_clone_audio_path(audio_file_path)
    return audio_path.name, audio_path.read_bytes(), content_type, file_size


def _cleanup_expired_files() -> None:
    now = time.time()
    for existing in OUTPUT_DIR.glob("*"):
        if existing.is_file() and now - existing.stat().st_mtime > REMOTE_FILE_TTL_SECONDS:
            existing.unlink(missing_ok=True)


async def _new_output_path(filename: str, audio_format: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not REMOTE_MODE:
        return OUTPUT_DIR / filename
    await anyio.to_thread.run_sync(_cleanup_expired_files)
    return OUTPUT_DIR / f"{secrets.token_urlsafe(32)}.{audio_format}"


def _audio_fields(output_path: Path) -> dict:
    if not REMOTE_MODE:
        return {"audio_path": str(output_path)}
    return {
        "audio_url": f"{PUBLIC_FILE_URL}/{output_path.name}",
        "expires_in_seconds": REMOTE_FILE_TTL_SECONDS,
    }


def _audio_result(output_path: Path) -> str | dict:
    fields = _audio_fields(output_path)
    return fields if REMOTE_MODE else fields["audio_path"]

app = TypecastMCP(
    "typecast-api-mcp-server",
    instructions=TYPECAST_API_KNOWLEDGE,
    host="0.0.0.0",
    port=8000,
    stateless_http=True,
)


def create_http_app():
    if not REMOTE_MODE:
        raise RuntimeError("MCP_REMOTE_MODE=true is required for Streamable HTTP")
    return ApiKeyMiddleware(app.streamable_http_app())


@app.custom_route("/health", methods=["GET"])
async def health(_request: Request):
    return JSONResponse({"status": "ok"})


@app.custom_route("/files/{filename}", methods=["GET"])
async def download_audio(request: Request):
    filename = request.path_params["filename"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,64}\.(wav|mp3)", filename):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    file_path = OUTPUT_DIR / filename
    if not file_path.is_file() or time.time() - file_path.stat().st_mtime > REMOTE_FILE_TTL_SECONDS:
        file_path.unlink(missing_ok=True)
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(
        file_path,
        filename=f"typecast-audio{file_path.suffix}",
        media_type="audio/wav" if file_path.suffix == ".wav" else "audio/mpeg",
        headers={"Cache-Control": "private, no-store"},
    )


@app.tool("search_documentation", "Search the Typecast API documentation without authentication")
async def search_documentation(query: str, limit: int = 5) -> list[dict]:
    if not query.strip() or len(query) > 500:
        raise ValueError("query must be between 1 and 500 characters")
    if limit < 1 or limit > 10:
        raise ValueError("limit must be between 1 and 10")
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{DOCS_SERVICE_URL}/__mcp/search",
            params={"q": query, "limit": limit},
            headers={"User-Agent": _user_agent()},
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results", []) if isinstance(payload, dict) else []
        return results if isinstance(results, list) else []


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
        description="Absolute loudness normalization target in LUFS. Mutually exclusive with volume on the non-streaming endpoint (any presence of volume causes 4xx). Supported by streaming TTS.",
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


class RecommendedVoice(BaseModel):
    """Recommended voice candidate returned by the recommendation API."""
    voice_id: str = Field(description="Recommended Typecast voice identifier")
    voice_name: str = Field(description="Display name returned with the recommendation")
    score: float = Field(description="Recommendation relevance score")


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

    url = f"{API_HOST}/v2/voices"
    if params:
        url = f"{url}?{urlencode(params)}"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=_api_headers())
        if response.status_code != 200:
            raise Exception(f"Failed to get voices: {response.status_code}")
        return response.json()


@app.tool("recommend_voices", "Recommend Typecast voices from a text description")
async def recommend_voices(query: str, count: int = 5) -> list[dict]:
    """Recommend voices that match a natural-language text description.

    The recommendation API returns only voice_id, voice_name, and score. Call
    get_voices or get_voice with the returned IDs when you need metadata such as
    supported models, emotions, gender, age, or use cases before making a TTS
    request.

    Args:
        query: Text description of the desired style, mood, language, use case,
            or content context.
        count: Maximum number of recommendations to return. Must be 1-10.

    Returns:
        Recommended voice candidates sorted by relevance score.
    """
    if not query.strip():
        raise ValueError("query is required")
    if count < 1 or count > 10:
        raise ValueError("count must be between 1 and 10")

    url = f"{API_HOST}/v1/voices/recommendations?{urlencode({'query': query, 'count': count})}"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=_api_headers())
        if response.status_code != 200:
            raise Exception(f"Failed to recommend voices: {response.status_code}")
        return [RecommendedVoice(**voice).model_dump() for voice in response.json()]


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
        response = await client.get(url, headers=_api_headers())
        if response.status_code != 200:
            raise Exception(f"Failed to get voice: {response.status_code}")
        return response.json()


@app.tool("clone_voice", "Create a quick-cloned custom voice from a local WAV or MP3 audio sample")
async def clone_voice(
    name: str,
    audio_file_path: str | None = None,
    model: str = TTSModel.SSFM_V30.value,
    audio_base64: str | None = None,
    audio_filename: str = "voice.wav",
) -> dict:
    """Create a quick-cloned custom voice.

    Calls POST /v1/voices/clone with multipart form data. Use the returned
    voice_id with text_to_speech, text_to_speech_stream, or
    text_to_speech_with_timestamps. Delete temporary cloned voices with
    delete_cloned_voice when they are no longer needed.

    Args:
        name: Display name for the cloned voice. Must be 1-30 characters.
        audio_file_path: Local WAV or MP3 sample path. Maximum file size is 25 MB.
        model: Voice cloning model. Default: ssfm-v30.
        audio_base64: Base64-encoded WAV or MP3 sample for a remote MCP server.
        audio_filename: Filename with .wav or .mp3 extension for audio_base64.

    Returns:
        Dict returned by the Typecast API plus normalized handoff fields:
            voice_id, cloned_voice_id, next_step_voice_id, next_step_model.
    """
    char_count = len(name)
    if char_count < 1 or char_count > 30:
        raise ValueError(f"Voice name must be 1-30 characters; got {char_count}.")

    model_enum = TTSModel(model)
    filename, audio_content, content_type, file_size = _quick_clone_audio(
        audio_file_path,
        audio_base64,
        audio_filename,
    )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, write=120.0, read=120.0, pool=10.0)
    ) as client:
        response = await client.post(
            f"{API_HOST}/v1/voices/clone",
            headers=_api_headers(),
            data={"name": name, "model": model_enum.value},
            files={"file": (filename, audio_content, content_type)},
        )

    if response.status_code not in {200, 201}:
        raise Exception(f"Failed to clone voice: {response.status_code}, {response.text}")

    payload = response.json()
    result = payload.get("result") or payload.get("data") or payload
    if not isinstance(result, dict):
        result = {"raw": payload}

    voice_id = result.get("voice_id") or result.get("voiceId")
    if not voice_id:
        raise ValueError(f"Failed to extract voice_id from clone response: {payload}")
    if not voice_id.startswith("uc_"):
        raise ValueError("Only cloned voice IDs that start with 'uc_' can be deleted.")

    voice_name = result.get("name") or result.get("voice_name") or name

    return {
        **result,
        "voice_id": voice_id,
        "cloned_voice_id": voice_id,
        "voice_name": voice_name,
        "name": voice_name,
        "model": result.get("model") or model_enum.value,
        "file_size": file_size,
        "next_step_voice_id": voice_id,
        "next_step_model": result.get("model") or model_enum.value,
    }


@app.tool("delete_cloned_voice", "Delete a quick-cloned custom voice by voice ID")
async def delete_cloned_voice(voice_id: str) -> dict:
    """Delete a quick-cloned custom voice.

    Args:
        voice_id: Cloned voice ID returned by clone_voice. Must start with uc_.

    Returns:
        Dict with success=true and the deleted voice_id.
    """
    if not voice_id.startswith("uc_"):
        raise ValueError("Only cloned voice IDs that start with 'uc_' can be deleted.")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, write=10.0, read=30.0, pool=10.0)
    ) as client:
        response = await client.delete(f"{API_HOST}/v1/voices/{voice_id}", headers=_api_headers())

    if response.status_code not in {200, 204}:
        raise Exception(f"Failed to delete cloned voice: {response.status_code}, {response.text}")

    return {"id": voice_id, "voice_id": voice_id, "success": True}


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
) -> str | dict:
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
        Local mode: path to the saved audio file.
        Remote mode: dict with audio_url and expires_in_seconds.
    """
    if target_lufs is not None and not (-70.0 <= target_lufs <= 0.0):
        raise ValueError(f"target_lufs must be between -70.0 and 0.0, got {target_lufs}")

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
            headers=_api_headers(),
        )
        if response.status_code != 200:
            raise Exception(f"Failed to generate speech: {response.status_code}, {response.text}")

        safe_text = _sanitize_for_filename(re.sub(r'\s+', '', text[:10]))
        safe_voice = _sanitize_for_filename(voice_id)
        output_path = await _new_output_path(
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{safe_voice}_{safe_text}.{audio_format}",
            audio_format,
        )
        output_path.write_bytes(response.content)

        return _audio_result(output_path)


@app.tool("play_audio", "Play the generated audio file")
async def play_audio(file_path: str) -> str:
    """Play the audio file at the specified path

    Args:
        file_path: Path to the audio file to play

    Returns:
        Status message
    """
    try:
        import sounddevice as sd
        import soundfile as sf

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
    target_lufs: float | None = None,
) -> str | dict:
    """Convert text to speech via the streaming endpoint and save the result.

    Calls POST /v1/text-to-speech/stream which returns chunked audio data
    in real time. The chunks are concatenated and saved as a single file.

    Note: the streaming endpoint does not accept volume, but supports
    target_lufs for absolute loudness normalization.

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
        target_lufs: Optional absolute loudness normalization target in LUFS (-70.0 ~ 0.0)

    Returns:
        Local mode: path to the saved audio file.
        Remote mode: dict with audio_url and expires_in_seconds.
    """
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
    if target_lufs is not None:
        output_payload["target_lufs"] = target_lufs

    request_payload = {
        "voice_id": voice_id,
        "text": text,
        "model": model,
        "prompt": prompt_payload,
        "output": output_payload,
    }

    safe_text = _sanitize_for_filename(re.sub(r"\s+", "", text[:10]))
    safe_voice = _sanitize_for_filename(voice_id)
    output_path = await _new_output_path(
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{safe_voice}_{safe_text}_stream.{audio_format}",
        audio_format,
    )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, write=30.0, read=None, pool=10.0)
    ) as client:
        async with client.stream(
            "POST",
            f"{API_HOST}/v1/text-to-speech/stream",
            json=request_payload,
            headers=_api_headers(),
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

    return _audio_result(output_path)


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

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, write=10.0, read=30.0, pool=10.0)
    ) as client:
        response = await client.get(url, headers=_api_headers())
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
    target_lufs: float | None = None,
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
            - local mode: 'audio_path' — path to the saved audio file
            - remote mode: 'audio_url' and 'expires_in_seconds'
            - 'words': list | None — word-level alignment when available
            - 'characters': list | None — character-level alignment when available
            - 'raw': dict — full server response with the audio bytes stripped
    """
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
    output_payload = Output(**output_kwargs).model_dump(exclude_none=True)

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
            headers=_api_headers(),
        )
        if response.status_code != 200:
            raise Exception(
                f"Failed to generate timestamped speech: {response.status_code}, {response.text}"
            )

    payload = response.json()

    audio_b64 = payload.get("audio", "")
    audio_bytes = base64.b64decode(audio_b64) if audio_b64 else b""

    safe_text = _sanitize_for_filename(re.sub(r"\s+", "", text[:10]))
    safe_voice = _sanitize_for_filename(voice_id)
    audio_path = await _new_output_path(
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{safe_voice}_{safe_text}_ts.{audio_format}",
        audio_format,
    )
    audio_path.write_bytes(audio_bytes)

    # Server returns words / characters at the top level of the response,
    # matching typecast-go/timestamps.go (TTSWithTimestampsResponse). There
    # is no `alignment` wrapper.
    words = payload.get("words")
    characters = payload.get("characters")

    raw = {k: v for k, v in payload.items() if k != "audio"}

    return {
        **_audio_fields(audio_path),
        "words": words,
        "characters": characters,
        "raw": raw,
    }
