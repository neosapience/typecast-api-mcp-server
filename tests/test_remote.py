import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import main
from app.server import (
    ApiKeyMiddleware,
    _api_headers,
    _quick_clone_audio,
    _request_api_key,
    _request_attribution,
    _user_agent,
    app,
    create_http_app,
    download_audio,
)
from click.testing import CliRunner
from mcp.server.fastmcp.exceptions import ToolError
from starlette.requests import Request


class RemoteModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_preserves_local_sse_and_rejects_unsafe_http_transports(self):
        runner = CliRunner()
        with patch("app.main.REMOTE_MODE", False), \
             patch("app.main.uvicorn.run") as uvicorn_run, \
             patch("app.main.app.run") as app_run:
            http_result = runner.invoke(main, ["--transport", "streamable-http"])
            local_sse_result = runner.invoke(main, ["--transport", "sse"])

        self.assertNotEqual(http_result.exit_code, 0)
        self.assertIn("MCP_REMOTE_MODE=true is required", http_result.output)
        uvicorn_run.assert_not_called()
        app_run.assert_called_once_with(transport="sse")
        self.assertEqual(local_sse_result.exit_code, 0)

        with patch("app.main.REMOTE_MODE", True), \
             patch("app.main.uvicorn.run") as uvicorn_run, \
             patch("app.main.app.run") as app_run:
            remote_sse_result = runner.invoke(main, ["--transport", "sse"])

        self.assertNotEqual(remote_sse_result.exit_code, 0)
        self.assertIn("SSE is not supported in remote mode", remote_sse_result.output)
        uvicorn_run.assert_not_called()
        app_run.assert_not_called()

    async def test_anonymous_clients_only_see_search(self):
        with patch("app.server.REMOTE_MODE", True):
            self.assertEqual(
                {tool.name for tool in await app.list_tools()},
                {"search_documentation"},
            )

            token = _request_api_key.set("tc-test-key")
            try:
                names = {tool.name for tool in await app.list_tools()}
            finally:
                _request_api_key.reset(token)

            self.assertIn("get_voices", names)
            self.assertNotIn("play_audio", names)

            with self.assertRaises(ToolError):
                await app.call_tool("get_voices", {})

            token = _request_api_key.set("tc-test-key")
            try:
                with self.assertRaises(ToolError):
                    await app.call_tool("play_audio", {"file_path": "unused.wav"})
            finally:
                _request_api_key.reset(token)

            with self.assertRaises(ValueError):
                _quick_clone_audio("/tmp/server.wav", None, "voice.wav")

        with patch("app.server.REMOTE_MODE", False), self.assertRaises(RuntimeError):
            create_http_app()

    async def test_request_auth_is_used_only_during_request(self):
        seen = []

        async def downstream(scope, receive, send):
            seen.append(_api_headers())

        middleware = ApiKeyMiddleware(downstream)
        with patch("app.server.REMOTE_MODE", True), \
             patch("app.server.MCP_VERSION", "0.2.1"), \
             patch("app.server.platform.python_version", return_value="3.12.0"), \
             patch("app.server.httpx.__version__", "0.28.1"):
            for headers in [
                [(b"x-api-key", b"tc-request-key")],
                [(b"authorization", b"bEaReR tc-bearer-key")],
                [
                    (b"x-api-key", b"tc-attributed-key"),
                    (b"x-typecast-integration-source", b"skill"),
                    (b"x-typecast-generated-by", b"codex"),
                ],
            ]:
                await middleware({"type": "http", "headers": headers}, None, None)
            self.assertEqual(seen, [
                {
                    "X-API-KEY": "tc-request-key",
                    "User-Agent": "typecast-mcp/0.2.1 Python/3.12.0 httpx/0.28.1 (deployment=hosted)",
                },
                {
                    "X-API-KEY": "tc-bearer-key",
                    "User-Agent": "typecast-mcp/0.2.1 Python/3.12.0 httpx/0.28.1 (deployment=hosted)",
                },
                {
                    "X-API-KEY": "tc-attributed-key",
                    "User-Agent": (
                        "typecast-mcp/0.2.1 Python/3.12.0 httpx/0.28.1 (deployment=hosted) "
                        "typecast-integration/1 (source=skill; generated_by=codex)"
                    ),
                },
            ])
            self.assertIsNone(_request_attribution.get())
            with self.assertRaises(ToolError):
                _api_headers()

    async def test_user_agent_rejects_invalid_attribution(self):
        with patch("app.server.REMOTE_MODE", False):
            for attribution in [("skill", None), ("other", "codex"), ("skill", "Codex")]:
                token = _request_attribution.set(attribution)
                try:
                    with self.assertRaises(ToolError):
                        _user_agent()
                finally:
                    _request_attribution.reset(token)

    async def test_expired_audio_is_removed(self):
        filename = f"{'a' * 32}.wav"
        with tempfile.TemporaryDirectory() as directory, \
             patch("app.server.OUTPUT_DIR", Path(directory)), \
             patch("app.server.REMOTE_FILE_TTL_SECONDS", 1):
            file_path = Path(directory) / filename
            file_path.write_bytes(b"expired")
            os.utime(file_path, (time.time() - 10, time.time() - 10))
            response = await download_audio(Request({
                "type": "http",
                "method": "GET",
                "path": f"/files/{filename}",
                "headers": [],
                "path_params": {"filename": filename},
            }))
            self.assertEqual(response.status_code, 404)
            self.assertFalse(file_path.exists())


if __name__ == "__main__":
    unittest.main()
