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
    clone_voice,
    create_professional_voice,
    create_http_app,
    delete_cloned_voice,
    download_audio,
    get_custom_voice,
    get_custom_voices,
    get_voice,
    get_voices,
)
from click.testing import CliRunner
from mcp.server.fastmcp.exceptions import ToolError
from starlette.requests import Request


class RemoteModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_v3_voice_and_custom_voice_routes(self):
        class Response:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload
                self.text = ""

            def json(self):
                return self._payload

        class Client:
            requests = []

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, **kwargs):
                self.requests.append(("GET", url, kwargs))
                if url.endswith("/v3/voices"):
                    return Response(200, [voice_payload])
                if "/v3/voices/" in url:
                    return Response(200, voice_payload)
                if url.endswith("/v1/custom-voices"):
                    return Response(200, [custom_payload])
                return Response(200, custom_payload)

            async def post(self, url, **kwargs):
                self.requests.append(("POST", url, kwargs))
                return Response(202 if "professional" in url else 201, custom_payload)

            async def delete(self, url, **kwargs):
                self.requests.append(("DELETE", url, kwargs))
                return Response(204, None)

        voice_payload = {
            "voice_id": "tc_voice",
            "voice_name": {"eng": "Voice", "kor": "보이스"},
            "models": [{"version": "ssfm-v30", "emotions": ["normal"]}],
            "voice_type": "original",
        }
        custom_payload = {
            "voice_id": "uc_voice",
            "name": "Custom",
            "model": "ssfm-v30",
            "source": "instant",
            "status": "completed",
        }
        with patch("app.server.API_HOST", "https://api.example.test"), \
             patch("app.server.API_KEY", "key"), \
             patch("app.server.httpx.AsyncClient", Client):
            self.assertEqual((await get_voices())[0]["voice_name"]["kor"], "보이스")
            self.assertEqual((await get_voice("tc_voice"))["voice_type"], "original")
            self.assertEqual((await clone_voice("Custom", audio_base64="AA=="))["status"], "completed")
            self.assertEqual(
                (await create_professional_voice("Custom", "kor", audio_base64="AA=="))["status"],
                "completed",
            )
            self.assertEqual((await get_custom_voices())[0]["source"], "instant")
            self.assertEqual((await get_custom_voice("uc_voice"))["voice_id"], "uc_voice")
            self.assertTrue((await delete_cloned_voice("uc_voice"))["success"])

        self.assertEqual(
            [request[1] for request in Client.requests],
            [
                "https://api.example.test/v3/voices",
                "https://api.example.test/v3/voices/tc_voice",
                "https://api.example.test/v1/custom-voices/instant-clone",
                "https://api.example.test/v1/custom-voices/professional-clone",
                "https://api.example.test/v1/custom-voices",
                "https://api.example.test/v1/custom-voices/uc_voice",
                "https://api.example.test/v1/custom-voices/uc_voice",
            ],
        )

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
             patch("app.server.MCP_VERSION", "0.2.2"), \
             patch("app.server.platform.python_version", return_value="3.12.0"), \
             patch("app.server.httpx.__version__", "0.28.1"):
            for headers in [
                [(b"x-api-key", b"tc-request-key")],
                [(b"authorization", b"bEaReR tc-bearer-key")],
                [
                    (b"x-api-key", b"tc-attributed-key"),
                    (b"x-typecast-integration-source", b"api-docs"),
                    (b"x-typecast-generated-by", b"codex"),
                ],
            ]:
                await middleware({"type": "http", "headers": headers}, None, None)
            self.assertEqual(seen, [
                {
                    "X-API-KEY": "tc-request-key",
                    "User-Agent": "typecast-mcp/0.2.2 Python/3.12.0 httpx/0.28.1 (deployment=hosted)",
                },
                {
                    "X-API-KEY": "tc-bearer-key",
                    "User-Agent": "typecast-mcp/0.2.2 Python/3.12.0 httpx/0.28.1 (deployment=hosted)",
                },
                {
                    "X-API-KEY": "tc-attributed-key",
                    "User-Agent": (
                        "typecast-mcp/0.2.2 Python/3.12.0 httpx/0.28.1 (deployment=hosted) "
                        "typecast-integration/1 (source=api-docs; generated_by=codex)"
                    ),
                },
            ])
            self.assertIsNone(_request_attribution.get())
            with self.assertRaises(ToolError):
                _api_headers()

    async def test_user_agent_validates_attribution(self):
        with patch("app.server.REMOTE_MODE", False):
            for source in ("api-page", "api-docs"):
                token = _request_attribution.set((source, "codex"))
                try:
                    self.assertIn(f"source={source}", _user_agent())
                finally:
                    _request_attribution.reset(token)
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
