import inspect
import unittest
from pathlib import Path
from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import TypeAdapter, ValidationError
from app.server import Output, text_to_speech, text_to_speech_stream, text_to_speech_with_timestamps


class RemoveSilenceTest(unittest.TestCase):
    def test_model_and_tool_contract(self):
        for tool in [text_to_speech, text_to_speech_stream, text_to_speech_with_timestamps]:
            self.assertIsNone(inspect.signature(tool).parameters["remove_silence_ms"].default)
            adapter = TypeAdapter(get_type_hints(tool, include_extras=True)["remove_silence_ms"])
            for value in [None, 0, 300, 1000]:
                self.assertEqual(adapter.validate_python(value), value)
            for value in [True, False, "100", 0.5, -1, 1001]:
                with self.assertRaises(ValidationError):
                    adapter.validate_python(value)
        self.assertNotIn("remove_silence_ms", Output().model_dump(exclude_none=True))
        self.assertEqual(Output(remove_silence_ms=0).model_dump()["remove_silence_ms"], 0)


class RemoveSilencePayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_all_tools_forward_optional_silence(self):
        for tool in [text_to_speech, text_to_speech_stream, text_to_speech_with_timestamps]:
            for value in [None, 0, 300, 1000]:
                client = MagicMock()
                client.__aenter__ = AsyncMock(return_value=client)
                client.__aexit__ = AsyncMock(return_value=None)
                client.post = AsyncMock(side_effect=RuntimeError("captured"))
                client.stream.side_effect = RuntimeError("captured")
                with patch("app.server.httpx.AsyncClient", return_value=client), patch(
                    "app.server._api_headers", return_value={}
                ), patch("app.server._new_output_path", new=AsyncMock(return_value=Path("/unused"))):
                    with self.assertRaisesRegex(RuntimeError, "captured"):
                        await tool(voice_id="voice", text="Hello", remove_silence_ms=value)
                method = client.stream if tool is text_to_speech_stream else client.post
                output = method.call_args.kwargs["json"]["output"]
                if value is None:
                    self.assertNotIn("remove_silence_ms", output)
                else:
                    self.assertEqual(output["remove_silence_ms"], value)
