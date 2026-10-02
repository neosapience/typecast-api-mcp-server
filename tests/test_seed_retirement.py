import inspect
import unittest

from app.server import TTSRequest, text_to_speech, text_to_speech_stream, text_to_speech_with_timestamps
from app.knowledge import TYPECAST_API_KNOWLEDGE


class SeedRetirementTest(unittest.TestCase):
    def test_seed_is_not_exposed_or_serialized(self):
        self.assertNotIn("seed", TTSRequest.model_json_schema()["properties"])
        self.assertNotIn("`seed`", TYPECAST_API_KNOWLEDGE)
        for seed in [0, 42]:
            request = TTSRequest(voice_id="tc_test", text="hello", model="ssfm-v30", seed=seed)
            self.assertNotIn("seed", request.model_dump(exclude_none=True))
        for tool in [text_to_speech, text_to_speech_stream, text_to_speech_with_timestamps]:
            self.assertNotIn("seed", inspect.signature(tool).parameters)
