import unittest

import httpx

from voiceagent.backends.ollama import Ollama
from voiceagent.config import LlmConfig


class OllamaTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_parses_content_and_done(self):
        payload = '{"message":{"content":"Bonjour"}}\n{"done":true}\n'
        model = Ollama(
            LlmConfig(),
            42,
            httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
        )
        try:
            tokens = [token async for token in model.stream([])]
            self.assertEqual(tokens, ["Bonjour"])
        finally:
            await model.close()

    async def test_truncated_stream_is_error(self):
        model = Ollama(
            LlmConfig(),
            42,
            httpx.MockTransport(
                lambda request: httpx.Response(200, text='{"message":{"content":"Bonjour"}}\n')
            ),
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "tronqué"):
                _ = [token async for token in model.stream([])]
        finally:
            await model.close()

    async def test_api_error_is_propagated(self):
        model = Ollama(
            LlmConfig(),
            42,
            httpx.MockTransport(
                lambda request: httpx.Response(200, text='{"error":"missing model"}\n')
            ),
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "missing model"):
                _ = [token async for token in model.stream([])]
        finally:
            await model.close()
