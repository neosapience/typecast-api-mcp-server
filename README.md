# typecast-api-mcp-server

MCP Server for typecast-api, enabling seamless integration with MCP clients. This project provides a standardized way to interact with Typecast API through the Model Context Protocol.

## About

This project implements a Model [Context Protocol server](https://modelcontextprotocol.io/introduction) for Typecast API, allowing MCP clients to interact with the Typecast API in a standardized way.

## Supported Models

| Model | Description | Emotion Control |
|-------|-------------|-----------------|
| ssfm-v30 | Latest model (recommended) | Preset + Smart Mode |
| ssfm-v21 | Stable production model | Preset only |

### ssfm-v30 Features
- **7 Emotion Presets**: normal, happy, sad, angry, whisper, toneup, tonedown
- **Smart Mode**: AI automatically infers emotion from context using `previous_text` and `next_text`
- **37 Languages**: Extended language support

## Feature Implementation Status

| Feature                          | Status |
| -------------------------------- | ------ |
| **Voice Management**             |        |
| Search Documentation             | ✅     |
| Get Voices (V3 API)              | ✅     |
| Get Voices `use_cases` filter    | ✅     |
| Get Voice (V3 API)               | ✅     |
| Recommend Voices                 | ✅     |
| Text to Speech                   | ✅     |
| Text to Speech (Streaming)       | ✅     |
| Text to Speech (with Timestamps) | ✅     |
| Get My Subscription              | ✅     |
| Play Audio                       | ✅     |
| **Output Controls**              |        |
| `target_lufs` loudness norm      | ✅     |
| **ssfm-v30 Support**             |        |
| Preset Mode                      | ✅     |
| Smart Mode                       | ✅     |
| **Custom Voice**                 |        |
| Instant / Professional Clone     | ✅     |
| List / Get / Delete Custom Voice | ✅     |

## Quick Voice Cloning

The MCP server exposes tools for the current Custom Voice API:

- `clone_voice`: creates a quick-cloned custom voice from a local WAV or MP3 file.
- `create_professional_voice`: starts a professional clone; poll its status before use.
- `get_custom_voices` and `get_custom_voice`: list voices or inspect clone status.
- `delete_cloned_voice`: deletes a cloned voice ID that starts with `uc_`.

Instant cloning constraints:

- Voice name must be 1-30 characters.
- Audio sample must be WAV or MP3.
- Audio sample must be 25 MB or smaller.
- Use `ssfm-v30` unless you have a specific compatibility reason.

Typical flow:

1. Run `clone_voice` with `name`, `audio_file_path`, and optional `model`.
2. Use the returned `next_step_voice_id` and `next_step_model` in `text_to_speech`, `text_to_speech_stream`, or `text_to_speech_with_timestamps`.
3. Run `delete_cloned_voice` when the temporary cloned voice is no longer needed.

Professional cloning returns `202 Accepted`. Poll `get_custom_voice` until its
status becomes `completed` or `failed`; completion can take up to two hours.

## Voice Recommendations

Use `recommend_voices` when you know the desired style, mood, language, or use
case but do not know the exact voice ID yet. It calls
`GET /v1/voices/recommendations` and returns candidates sorted by score.

The recommendation response intentionally contains only `voice_id`,
`voice_name`, and `score`. If an agent needs details about a recommended voice,
call `get_voice` for each returned ID or `get_voices` for a broader filtered
list before using the ID in TTS.

## Setup

### Hosted Server

The hosted Streamable HTTP endpoint is:

```text
https://typecast-api-docs-web-production.up.railway.app/mcp
```

Without authentication, the server exposes only `search_documentation`. Send a
Typecast API key on every MCP request to unlock the Typecast API tools:

```text
X-API-KEY: YOUR_TYPECAST_API_KEY
```

`Authorization: Bearer YOUR_TYPECAST_API_KEY` is also supported. The hosted
server does not store the key. Generated audio is returned as a private,
unguessable download URL that expires after one hour. `play_audio` remains a
local-only tool because a hosted server cannot play sound on the MCP client's
device.

To preserve how Typecast integration code was created, hosted clients may send
both attribution headers together:

```text
X-Typecast-Integration-Source: api-docs
X-Typecast-Generated-By: codex
```

Use `api-page` for API page onboarding and `api-docs` for API documentation
onboarding. The legacy `llms` and `skill` values remain accepted.
`X-Typecast-Generated-By` accepts a lowercase ASCII token up to 32 characters.
The server keeps its own `typecast-mcp/<version>` User-Agent and appends this
attribution instead of replacing it.

On the hosted server, `clone_voice` accepts only `audio_base64` together with
an `audio_filename` ending in `.wav` or `.mp3`. `audio_file_path` is available
only when this MCP server runs locally.

### Environment Variables

Set the following environment variables:

```bash
TYPECAST_API_KEY=<your-api-key>
TYPECAST_OUTPUT_DIR=<your-output-directory> # default: ~/Downloads/typecast_output
TYPECAST_INTEGRATION_SOURCE=<llms|skill|api-page|api-docs> # optional; set both attribution variables
TYPECAST_GENERATED_BY=<coding-agent-id>                     # optional; e.g. codex or claude-code
```

### Usage with Claude Desktop / Cursor

You can add the following to your `claude_desktop_config.json` or Cursor MCP settings:

#### Recommended: Using uvx (No installation required)

```json
{
  "mcpServers": {
    "typecast-api-mcp-server": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/neosapience/typecast-api-mcp-server.git",
        "typecast-api-mcp-server"
      ],
      "env": {
        "TYPECAST_API_KEY": "YOUR_API_KEY",
        "TYPECAST_OUTPUT_DIR": "PATH/TO/YOUR/OUTPUT/DIR"
      }
    }
  }
}
```

This method automatically fetches and runs the server from GitHub without manual cloning.

**Note for Linux users**: If you're running on Linux, you need to add the `XDG_RUNTIME_DIR` environment variable to the `env` section:

```json
"env": {
  "TYPECAST_API_KEY": "YOUR_API_KEY",
  "TYPECAST_OUTPUT_DIR": "PATH/TO/YOUR/OUTPUT/DIR",
  "XDG_RUNTIME_DIR": "/run/user/1000"
}
```

### Alternative: Local Installation

If you prefer to clone and run locally:

#### Git Clone

```bash
git clone https://github.com/neosapience/typecast-api-mcp-server.git
cd typecast-api-mcp-server
```

#### Dependencies

This project requires Python 3.10 or higher and uses `uv` for package management.

```bash
# Create virtual environment and install packages
uv venv
uv pip install -e .
```

#### Local Configuration

```json
{
  "mcpServers": {
    "typecast-api-mcp-server": {
      "command": "uv",
      "args": [
        "--directory",
        "/PATH/TO/YOUR/PROJECT",
        "run",
        "typecast-api-mcp-server"
      ],
      "env": {
        "TYPECAST_API_KEY": "YOUR_API_KEY",
        "TYPECAST_OUTPUT_DIR": "PATH/TO/YOUR/OUTPUT/DIR"
      }
    }
  }
}
```

Replace `/PATH/TO/YOUR/PROJECT` with the actual path where your project is located.

#### Manual Execution

You can also run the server manually:

```bash
uv run python app/main.py
```

## Contributing

Contributions are always welcome! Feel free to submit a Pull Request.

## License

MIT License
