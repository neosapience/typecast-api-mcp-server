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

| Feature              | Status |
| -------------------- | ------ |
| **Voice Management** |        |
| Get Voices (V2 API)  | ✅     |
| Get Voice (V2 API)   | ✅     |
| Text to Speech       | ✅     |
| Play Audio           | ✅     |
| **ssfm-v30 Support** |        |
| Preset Mode          | ✅     |
| Smart Mode           | ✅     |

## Setup

### Environment Variables

Set the following environment variables:

```bash
TYPECAST_API_KEY=<your-api-key>
TYPECAST_OUTPUT_DIR=<your-output-directory> # default: ~/Downloads/typecast_output
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
