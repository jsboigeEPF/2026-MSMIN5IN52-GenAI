"""TTS avec repli : si le fournisseur principal échoue avant le premier octet, on tente un 2e fournisseur."""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from src.interfaces import TTS


class FallbackTTS:
    name = "fallback-tts"

    def __init__(self, primary: TTS, secondary: TTS, on_event: Callable | None = None) -> None:
        self.primary, self.secondary = primary, secondary
        self.emit = on_event or (lambda *a, **k: None)

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        started = False
        try:
            async for pcm in self.primary.stream(text):
                started = True
                yield pcm
            return
        except Exception as e:
            if started:  # audio déjà parti : changer de voix en cours de morceau serait pire que de couper
                raise
            self.emit("tts_fallback", provider=getattr(self.secondary, "name", "secondary"), error=f"{type(e).__name__}: {e}"[:200])
        async for pcm in self.secondary.stream(text):
            yield pcm

    async def warmup(self) -> None:
        await self.primary.warmup()
