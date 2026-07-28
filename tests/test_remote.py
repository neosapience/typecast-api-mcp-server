import unittest
from unittest.mock import patch

from app.server import ApiKeyMiddleware, _api_headers, _request_api_key, app
from mcp.server.fastmcp.exceptions import ToolError


class RemoteModeTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_request_header_is_used_only_during_request(self):
        seen = []

        async def downstream(scope, receive, send):
            seen.append(_api_headers())

        middleware = ApiKeyMiddleware(downstream)
        with patch("app.server.REMOTE_MODE", True):
            await middleware(
                {"type": "http", "headers": [(b"x-api-key", b"tc-request-key")]},
                None,
                None,
            )
            self.assertEqual(seen, [{"X-API-KEY": "tc-request-key"}])
            with self.assertRaises(ToolError):
                _api_headers()


if __name__ == "__main__":
    unittest.main()
