"""TTS : MiniMax Speech 2.8 Turbo via OpenRouter /audio/speech. Flux mp3 décodé en PCM16 24 kHz au fil de l'eau."""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable

import httpx

from src.audio.mp3 import Mp3StreamDecoder
from src.audio.pcm import SAMPLE_RATE_OUT
from src.metrics.clock import elapsed_ms, now_ns
from src.metrics.cost import estimate_tts

RETRY_STATUS = {429, 502, 503}


class TTSError(RuntimeError):
    pass


class MiniMaxTTS:
    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str, model: str, voice: str,
                 speed: float | None = None, retries: int = 2, prices: dict | None = None, on_event: Callable | None = None,
                 timeout_s: float = 60.0) -> None:
        self.timeout_s = timeout_s   # délai réseau httpx (connexion, lecture entre deux morceaux d'audio)
        self.client, self.url = client, base_url.rstrip("/") + "/audio/speech"
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.model, self.voice, self.speed, self.retries, self.prices = model, voice, speed, retries, prices or {}
        self.emit = on_event or (lambda *a, **k: None)

    async def warmup(self) -> None:
        async for _ in self.stream("Oui."):
            pass

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """Chunks PCM16 mono 24 kHz. Annulable : fermer le générateur coupe la requête et le décodeur."""
        self.emit("tts_request", provider=self.model, voice=self.voice, chars=len(text))
        t0 = now_ns()
        body = {"model": self.model, "input": text, "voice": self.voice, "response_format": "mp3"}
        if self.speed:
            body["speed"] = self.speed  # débit relatif (1.0 = normal), supporté par MiniMax via OpenRouter
        resp = None
        for attempt in range(self.retries + 1):
            resp = await self.client.send(self.client.build_request("POST", self.url, headers=self.headers, json=body,
                                                                    timeout=self.timeout_s), stream=True)
            if resp.status_code == 200:
                break
            detail = (await resp.aread())[:200]
            await resp.aclose()
            if resp.status_code in RETRY_STATUS and attempt < self.retries:
                self.emit("tts_retry", provider=self.model, status=resp.status_code)
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
            raise TTSError(f"TTS HTTP {resp.status_code} {detail!r}")

        dec = Mp3StreamDecoder(SAMPLE_RATE_OUT)

        async def pump() -> None:
            try:
                first = True
                async for ch in resp.aiter_bytes():
                    if first:
                        first = False
                        self.emit("tts_first_byte", provider=self.model, ms=round(elapsed_ms(t0), 1))
                    dec.feed(ch)
            finally:
                dec.finish()

        task = asyncio.create_task(pump())
        try:
            first_pcm = True
            async for pcm in dec:
                if first_pcm:
                    first_pcm = False
                    self.emit("tts_first_pcm", provider=self.model, ms=round(elapsed_ms(t0), 1))
                yield pcm
            await task  # propage une erreur réseau survenue pendant le flux
            self.emit("tts_done", provider=self.model, ms=round(elapsed_ms(t0), 1))
            self.emit("usage", stage="tts", provider=self.model, chars=len(text),
                      cost_usd=estimate_tts(self.prices, self.model, len(text)))
        finally:
            task.cancel()
            dec.finish()
            with contextlib.suppress(BaseException):
                await task
            await resp.aclose()
