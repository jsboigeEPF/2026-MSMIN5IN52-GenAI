"""STT avec repli : si le Whisper de l'école échoue, on tente un 2e fournisseur (ex. Qwen3 ASR sur OpenRouter)."""
from __future__ import annotations

from collections.abc import Callable

from src.interfaces import STT


class FallbackSTT:
    name = "fallback-stt"

    def __init__(self, primary: STT, secondary: STT, on_event: Callable | None = None) -> None:
        self.primary, self.secondary = primary, secondary
        self.emit = on_event or (lambda *a, **k: None)

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        try:
            return await self.primary.transcribe(pcm16, sample_rate)
        except Exception as e:  # noqa: BLE001  toute panne du principal justifie d'essayer le secours
            self.emit("stt_fallback", provider=getattr(self.secondary, "name", "secondary"), error=f"{type(e).__name__}: {e}"[:200])
            return await self.secondary.transcribe(pcm16, sample_rate)

    async def warmup(self) -> None:
        await self.primary.warmup()
