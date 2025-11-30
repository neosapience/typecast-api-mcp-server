import base64
import io
import os
import shutil
import subprocess
from datetime import datetime
from enum import Enum
from pathlib import Path

import httpx
import sounddevice as sd
import soundfile as sf
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from pydantic import BaseModel, Field

load_dotenv()

API_HOST = os.environ.get("TYPECAST_API_HOST", "https://api.typecast.ai")
API_KEY = os.environ.get("TYPECAST_API_KEY")
OUTPUT_DIR = Path(os.environ.get("TYPECAST_OUTPUT_DIR", os.path.expanduser("~/Downloads/typecast_output")))
HTTP_HEADERS = { "X-API-KEY": API_KEY }

if not API_KEY:
    raise ValueError("TYPECAST_API_KEY environment variable is required")

app = FastMCP(
    "typecast-api-mcp-server",
    host="0.0.0.0",
    port=8000,
)


class TTSModel(str, Enum):
    SSFM_V21 = "ssfm-v21"


class EmotionEnum(str, Enum):
    NORMAL = "normal"
    SAD = "sad"
    HAPPY = "happy"
    ANGRY = "angry"
    REGRET = "regret"
    URGENT = "urgent"
    WHISPER = "whisper"
    SCREAM = "scream"
    SHOUT = "shout"
    TRUSTFUL = "trustful"
    SOFT = "soft"
    COLD = "cold"
    SARCASM = "sarcasm"
    INSPIRE = "inspire"
    CUTE = "cute"
    CHEER = "cheer"
    CASUAL = "casual"
    TUNELV1 = "tunelv1"
    TUNELV2 = "tunelv2"
    TONEMID = "tonemid"
    TONEUP = "toneup"
    TONEDOWN = "tonedown"


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


def is_installed(lib_name: str) -> bool:
    """Check if a system library/command is installed."""
    lib = shutil.which(lib_name)
    return lib is not None


def get_current_output_device() -> int | None:
    """Get the current output device being used by the system.
    
    Returns:
        Device ID of the current output device, or None to use system default
    """
    try:
        # Get the default output device
        default_device = sd.default.device[1]  # [input, output]
        
        # Query all devices to find active output device
        devices = sd.query_devices()
        
        # If default device is set, use it
        if default_device is not None:
            return default_device
        
        # Otherwise, find the first available output device
        for idx, device in enumerate(devices):
            if device['max_output_channels'] > 0:
                # Check if this device is the default host API output
                try:
                    host_api = sd.query_hostapis(device['hostapi'])
                    if host_api['default_output_device'] == idx:
                        return idx
                except:
                    pass
        
        # Return None to use sounddevice's automatic selection
        return None
    except Exception:
        # If anything fails, return None to use default
        return None


def play_audio_bytes(audio: bytes, use_ffmpeg: bool = True) -> str:
    """Play audio from bytes using ffmpeg or sounddevice.
    
    Automatically falls back to sounddevice if ffplay is not available.
    Uses the current system output device when playing with sounddevice.
    
    Args:
        audio: Audio data as bytes
        use_ffmpeg: If True, try to use ffplay (from ffmpeg) first. If False, use sounddevice.
    
    Returns:
        str: The method used to play audio ("ffplay" or "sounddevice")
    
    Raises:
        ValueError: If neither ffplay nor sounddevice can play the audio
    """
    if use_ffmpeg and is_installed("ffplay"):
        # Try ffplay first
        try:
            args = ["ffplay", "-autoexit", "-", "-nodisp"]
            proc = subprocess.Popen(
                args=args,
                stdout=subprocess.PIPE,
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            proc.communicate(input=audio)
            proc.poll()
            return "ffplay"
        except Exception as e:
            # If ffplay fails, fall back to sounddevice
            pass
    
    # Use sounddevice (either requested or as fallback)
    try:
        audio_data, samplerate = sf.read(io.BytesIO(audio))
        
        # Get the current output device
        output_device = get_current_output_device()
        
        # Play on the current output device
        sd.play(audio_data, samplerate, device=output_device)
        sd.wait()
        
        device_info = f" (device: {output_device})" if output_device is not None else " (default device)"
        return f"sounddevice{device_info}"
    except Exception as e:
        raise ValueError(
            f"Failed to play audio. Neither ffplay nor sounddevice could play the audio. "
            f"Error: {str(e)}. "
            f"Install ffmpeg with 'brew install ffmpeg' (Mac) or from https://ffmpeg.org/"
        )


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


@app.tool("text_to_speech", "Convert text to speech using the specified voice and parameters. Generated audio will be saved to TYPECAST_OUTPUT_DIR.")
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
    if not text:
        raise ValueError("Text is required")

    # Pydantic 모델을 사용하여 자동 검증
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

        # 출력 디렉토리 생성
        if not OUTPUT_DIR.exists():
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # 파일 저장
        audio_bytes = response.content
        output_file_name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{voice_id}_{text[:10]}.{audio_format}"
        output_path = OUTPUT_DIR / output_file_name
        output_path.write_bytes(audio_bytes)

        return f"Successfully generated speech for voice: {voice_id}. File saved: {output_path}"


@app.tool("play_audio", "Play the generated audio file. Supports WAV and MP3 formats. Automatically uses ffplay if available, otherwise falls back to sounddevice.")
async def play_audio(file_path: str, use_ffmpeg: bool = True) -> str:
    """Play the audio file at the specified path

    Args:
        file_path: Path to the audio file to play
        use_ffmpeg: If True, prefer ffplay (default). Automatically falls back to sounddevice if ffplay is not available.

    Returns:
        Status message indicating which method was used
    """
    try:
        with open(file_path, "rb") as f:
            audio_bytes = f.read()
        
        method = play_audio_bytes(audio_bytes, use_ffmpeg=use_ffmpeg)
        return f"Successfully played audio file using {method}: {file_path}"
    except FileNotFoundError:
        return f"File not found: {file_path}"
    except ValueError as e:
        return f"Playback error: {str(e)}"
    except Exception as e:
        return f"Failed to play audio file: {str(e)}"
