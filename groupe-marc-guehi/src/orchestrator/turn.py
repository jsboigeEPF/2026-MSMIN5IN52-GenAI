"""Un tour de conversation sans interruption : [audio -> STT] -> LLM en flux -> TTS par morceaux, audio émis dans l'ordre."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from src.interfaces import LLM, STT, TTS
from src.metrics.recorder import Recorder
from src.orchestrator.chunker import SentenceChunker


@dataclass
class TurnResult:
    user_text: str
    answer: str
    audio: list[list[bytes]]  # PCM16 24 kHz, un élément par morceau de texte, dans l'ordre de lecture
    stt_ms: float | None
    first_audio_ms: float  # début du tour -> premier chunk PCM du 1er morceau
    total_ms: float


async def run_turn(*, stt: STT, llm: LLM, tts: TTS, history: list[dict], system_prompt: str, rec: Recorder,
                   user_text: str | None = None, audio: tuple[bytes, int] | None = None,
                   first_min: int = 8, rest_min: int = 20,
                   on_user_text: Callable[[str], Awaitable[None]] | None = None,
                   on_chunk: Callable[[str], Awaitable[None]] | None = None,
                   on_audio: Callable[[bytes, int], Awaitable[None]] | None = None) -> TurnResult:
    """on_chunk(texte) : un morceau part vers le TTS ; on_audio(pcm, idx) : audio prêt, dans l'ordre de lecture (idx = rang du morceau)."""
    t0 = time.perf_counter()
    stt_ms = None
    if user_text is None:
        if audio is None:
            raise ValueError("user_text ou audio requis")
        user_text = await stt.transcribe(*audio)
        stt_ms = (time.perf_counter() - t0) * 1000
    if on_user_text:
        await on_user_text(user_text)
    history.append({"role": "user", "content": user_text})

    first_audio: list[float] = []
    ordered: asyncio.Queue[tuple[int, asyncio.Queue] | None] = asyncio.Queue()  # une file par morceau, drainées dans l'ordre

    async def synth(text: str, q: asyncio.Queue, is_first: bool) -> list[bytes]:
        parts: list[bytes] = []
        try:
            async for pcm in tts.stream(text):
                if is_first and not first_audio:
                    first_audio.append((time.perf_counter() - t0) * 1000)
                    rec("turn_first_audio", ms=round(first_audio[0], 1))
                parts.append(pcm)
                q.put_nowait(pcm)
        finally:
            q.put_nowait(None)
        return parts

    async def drain() -> None:
        while (item := await ordered.get()) is not None:
            idx, q = item
            while (pcm := await q.get()) is not None:
                if on_audio:
                    await on_audio(pcm, idx)

    drainer = asyncio.create_task(drain())
    chunker, tasks, answer = SentenceChunker(first_min, rest_min), [], ""

    async def dispatch(text: str) -> None:
        q: asyncio.Queue = asyncio.Queue()
        ordered.put_nowait((len(tasks), q))
        tasks.append(asyncio.create_task(synth(text, q, not tasks)))
        if on_chunk:
            await on_chunk(text)

    try:
        async for delta in llm.stream([{"role": "system", "content": system_prompt}] + history):
            answer += delta
            for ch in chunker.feed(delta):
                await dispatch(ch)
        for ch in chunker.flush():
            await dispatch(ch)
        ordered.put_nowait(None)
        audio_parts = await asyncio.gather(*tasks)
        await drainer
    except BaseException:
        for t in (*tasks, drainer):
            t.cancel()
        raise
    answer = answer.strip()
    history.append({"role": "assistant", "content": answer})
    total = (time.perf_counter() - t0) * 1000
    rec("turn_done", ms=round(total, 1), chunks=len(tasks))
    return TurnResult(user_text, answer, list(audio_parts), stt_ms, first_audio[0] if first_audio else total, total)
