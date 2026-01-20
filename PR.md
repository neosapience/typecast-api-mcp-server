# feat/ssfm-v30: Add ssfm-v30 Model Support

## Summary

Add ssfm-v30 model support to the MCP server. Includes new emotion presets (whisper, toneup, tonedown), Smart Mode for context-aware emotion inference, and enhanced voice filtering via V2 Voices API.

## Changes

### Features

- **ssfm-v30 model support**: Add SSFM_V30 to TTSModel enum
- **New emotion presets**: whisper, toneup, tonedown (ssfm-v30 only)
- **Preset Mode**: `PresetPrompt` model for explicit emotion control
- **Smart Mode**: `SmartPrompt` model for context-aware emotion inference
  - `previous_text`: Previous context text
  - `next_text`: Next context text
- **V2 Voices API**: Gender/age filtering support
  - `GenderEnum`: male, female
  - `AgeEnum`: child, teen, young_adult, middle_aged, senior
- **Default model change**: ssfm-v21 → ssfm-v30

### Bug Fixes

- Fix output file extension to correctly use `audio_format` parameter

### Documentation

- knowledge.py: Add ssfm-v30 model description and features
- knowledge.py: Add Smart Mode usage examples (Python, JavaScript, cURL)
- knowledge.py: Document V2 Voices API specification and filtering
- knowledge.py: Update supported language count (27 → 37)
- README.md: Add supported models table
- README.md: Add ssfm-v30 features section
- README.md: Update feature implementation status

## Changed Files

| File | Changes |
|------|---------|
| `app/server.py` | ssfm-v30 model, new emotion presets, PresetPrompt/SmartPrompt, V2 API support |
| `app/knowledge.py` | ssfm-v30 documentation, Smart Mode examples, V2 API specification |
| `README.md` | Models table, feature status, ssfm-v30 features section |

## Key Code Changes

### New Pydantic Models

```python
class PresetPrompt(BaseModel):
    """Preset-based emotion control for ssfm-v30 model."""
    emotion_type: EmotionType = Field(default=EmotionType.PRESET)
    emotion_preset: EmotionEnum = Field(default=EmotionEnum.NORMAL)
    emotion_intensity: float = Field(default=1.0, ge=0.0, le=2.0)

class SmartPrompt(BaseModel):
    """Context-aware emotion inference for ssfm-v30 model."""
    emotion_type: EmotionType = Field(default=EmotionType.SMART)
    previous_text: str | None = Field(default=None)
    next_text: str | None = Field(default=None)
```

### V2 Voices API Filtering

```python
async def get_voices(
    model: str | None = None,
    gender: str | None = None,  # male, female
    age: str | None = None,     # child, teen, young_adult, middle_aged, senior
) -> dict:
```

### Extended text_to_speech Parameters

```python
async def text_to_speech(
    voice_id: str,
    text: str,
    model: str = TTSModel.SSFM_V30.value,  # Default changed
    emotion_type: str = "preset",           # New parameter
    emotion_preset: str = EmotionEnum.NORMAL.value,
    emotion_intensity: float = 1.0,
    previous_text: str | None = None,       # Smart Mode
    next_text: str | None = None,           # Smart Mode
    ...
) -> str:
```

## Testing

- [ ] get_voices: Verify V2 API calls and filtering
- [ ] text_to_speech: Verify Preset Mode emotion generation
- [ ] text_to_speech: Verify Smart Mode context inference
- [ ] Verify new emotion presets (whisper, toneup, tonedown)

## Related Issues

- MCP server update for ssfm-v30 model release
