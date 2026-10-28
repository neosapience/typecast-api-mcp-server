import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.server import (
    TTSModel, TTSRequest, clone_voice, create_professional_voice,
    text_to_speech, text_to_speech_stream, text_to_speech_with_timestamps,
)


class SSFMV31Test(unittest.IsolatedAsyncioTestCase):
    async def test_all_tts_tools_keep_v31_emotion_payloads(self):
        self.assertEqual(TTSRequest(voice_id="tc_test", text="hello", model="ssfm-v31").model,
                         TTSModel.SSFM_V31)
        for tool in [text_to_speech, text_to_speech_stream, text_to_speech_with_timestamps]:
            for emotion_type in ["smart", "preset"]:
                client = MagicMock()
                client.__aenter__ = AsyncMock(return_value=client)
                client.__aexit__ = AsyncMock(return_value=None)
                client.post = AsyncMock(side_effect=RuntimeError("captured"))
                client.stream.side_effect = RuntimeError("captured")
                with patch("app.server.httpx.AsyncClient", return_value=client), patch(
                    "app.server._api_headers", return_value={}
                ), patch("app.server._new_output_path", new=AsyncMock(return_value=Path("/unused"))):
                    with self.assertRaisesRegex(RuntimeError, "captured"):
                        await tool(voice_id="tc_test", text="hello", model="ssfm-v31",
                                   emotion_type=emotion_type)
                method = client.stream if tool is text_to_speech_stream else client.post
                payload = method.call_args.kwargs["json"]
                self.assertEqual(payload["model"], "ssfm-v31")
                self.assertEqual(payload["prompt"]["emotion_type"], emotion_type)

    async def test_tts_model_does_not_expand_cloning_support(self):
        for tool, kwargs in [(clone_voice, {}), (create_professional_voice, {"language": "eng"})]:
            with self.assertRaisesRegex(ValueError, "Cloning requires"):
                await tool(name="test", model="ssfm-v31", **kwargs)
