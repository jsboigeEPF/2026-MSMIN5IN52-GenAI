"""TTS de secours : modèle OpenRouter /audio/speech qui renvoie du PCM16 mono 24 kHz directement (ex. Qwen-Audio TTS Flash)."""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import httpx

from src.metrics.clock import elapsed_ms, now_ns
from src.metrics.cost import estimate_tts
from src.tts.minimax import TTSError


class PcmTTS:
    name = "openrouter-pcm-tts"

    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str, model: str, voice: str,
                 prices: dict | None = None, on_event: Callable | None = None, timeout_s: float = 60.0) -> None:
        self.timeout_s = timeout_s   # délai réseau httpx (connexion, lecture entre deux morceaux d'audio)
        self.client, self.url = client, base_url.rstrip("/") + "/audio/speech"
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.model, self.voice, self.prices = model, voice, prices or {}
        self.emit = on_event or (lambda *a, **k: None)

    async def warmup(self) -> None:
        pass  # secours : pas d'appel factice payant au démarrage

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        self.emit("tts_request", provider=self.model, voice=self.voice, chars=len(text))
        t0 = now_ns()
        body = {"model": self.model, "input": text, "voice": self.voice, "response_format": "pcm"}
        first, carry = True, b""
        async with self.client.stream("POST", self.url, headers=self.headers, json=body, timeout=self.timeout_s) as r:
            if r.status_code != 200:
                raise TTSError(f"TTS secours HTTP {r.status_code} {(await r.aread())[:200]!r}")
            async for ch in r.aiter_bytes():
                ch = carry + ch
                carry, ch = (ch[-1:], ch[:-1]) if len(ch) % 2 else (b"", ch)  # un échantillon PCM16 = 2 octets : pas de coupe au milieu
                if not ch:
                    continue
                if first:
                    first = False
                    self.emit("tts_first_pcm", provider=self.model, ms=round(elapsed_ms(t0), 1))
                yield ch
        self.emit("tts_done", provider=self.model, ms=round(elapsed_ms(t0), 1))
        self.emit("usage", stage="tts", provider=self.model, chars=len(text),
                  cost_usd=estimate_tts(self.prices, self.model, len(text)))
