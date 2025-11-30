# typecast-api-mcp-server-sample

MCP Server for typecast-api, enabling seamless integration with MCP clients. This project provides a standardized way to interact with Typecast API through the Model Context Protocol.

## About

This project implements a Model [Context Protocol server](https://modelcontextprotocol.io/introduction) for Typecast API, allowing MCP clients to interact with the Typecast API in a standardized way.

## Feature Implementation Status

| Feature              | Status |
| -------------------- | ------ |
| **Voice Management** |        |
| Get Voices           | ✅     |
| Text to Speech       | ✅     |
| Play Audio           | ✅     |

## Setup

### Git Clone

```bash
git clone https://github.com/hyunseung/typecast-api-mcp-server-sample.git
cd typecast-api-mcp-server-sample
```

### Dependencies

This project requires Python 3.10 or higher and uses `uv` for package management.

#### Package Installation

```bash
# Create virtual environment and install packages
uv venv
uv pip install -e .
```

### Environment Variables

Set the following environment variables:

```bash
TYPECAST_API_HOST=https://api.typecast.ai
TYPECAST_API_KEY=<your-api-key>
TYPECAST_OUTPUT_DIR=<your-output-directory> # default: ~/Desktop/typecast_output
```

Generated audio files will be saved to `TYPECAST_OUTPUT_DIR`.

### Usage with Claude Desktop

You can add the following to your `claude_desktop_config.json`:

#### Basic Configuration:

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
        "TYPECAST_API_HOST": "https://api.typecast.ai",
        "TYPECAST_API_KEY": "YOUR_API_KEY",
        "TYPECAST_OUTPUT_DIR": "PATH/TO/YOUR/OUTPUT/DIR"
      }
    }
  }
}
```

Replace `/PATH/TO/YOUR/PROJECT` with the actual path where your project is located.

### Manual Execution

You can also run the server manually:

```bash
uv run python app/main.py
```

## Contributing

Contributions are always welcome! Feel free to submit a Pull Request.

## License

MIT License
