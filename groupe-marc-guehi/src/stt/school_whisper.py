"""STT : Whisper large-v3-turbo hébergé par l'école (API compatible OpenAI /audio/transcriptions).
La même classe sert au STT de secours sur OpenRouter (autre URL, clé et modèle)."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx

from src.audio.pcm import SAMPLE_RATE_STT, resample, silence, wav_bytes
from src.metrics.clock import elapsed_ms, now_ns

RETRY_STATUS = {429, 500, 502, 503, 504}


class STTError(RuntimeError):
    pass


class SchoolWhisper:
    name = "school-whisper"

    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str, model: str,
                 language: str = "fr", retries: int = 2, on_event: Callable | None = None, name: str | None = None,
                 timeout_s: float = 60.0) -> None:
        self.timeout_s = timeout_s   # durée maximale d'une tentative (requête + réponse) ; dépassée : STTError, donc repli sur le STT de secours
        self.name = name or self.name
        self.client, self.url = client, base_url.rstrip("/") + "/audio/transcriptions"
        self.headers, self.model, self.language = {"Authorization": f"Bearer {api_key}"}, model, language
        self.retries, self.emit = retries, on_event or (lambda *a, **k: None)

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        wav = wav_bytes(resample(pcm16, sample_rate, SAMPLE_RATE_STT), SAMPLE_RATE_STT)
        self.emit("stt_request", provider=self.name, audio_s=len(pcm16) / (2 * sample_rate))
        t0 = now_ns()
        for attempt in range(self.retries + 1):
            try:
                async with asyncio.timeout(self.timeout_s):
                    r = await self.client.post(self.url, headers=self.headers, timeout=self.timeout_s,
                                               data={"model": self.model, "language": self.language},
                                               files={"file": ("audio.wav", wav, "audio/wav")})
            except TimeoutError as e:
                raise STTError(f"STT : pas de réponse après {self.timeout_s} s") from e
            if r.status_code in RETRY_STATUS and attempt < self.retries:
                self.emit("stt_retry", provider=self.name, status=r.status_code)
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise STTError(f"STT HTTP {r.status_code} {r.text[:200]}")
            text = r.json()["text"].strip()
            self.emit("stt_final", provider=self.name, text=text, ms=round(elapsed_ms(t0), 1))
            return text
        raise STTError("STT : échec après relances")

    async def warmup(self) -> None:
        """Évite le démarrage à froid du serveur (35 s observés au 1er appel) et ouvre la connexion TLS."""
        await self.transcribe(silence(1000, SAMPLE_RATE_STT), SAMPLE_RATE_STT)
