"""Un tour de conversation sans interruption : [audio -> STT] -> LLM en flux -> TTS par morceaux, audio émis dans l'ordre."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from src.interfaces import LLM, STT, TTS, LengthCut, SearchNotice
from src.metrics.clock import elapsed_ms, now_ns
from src.metrics.recorder import Recorder
from src.orchestrator.chunker import SentenceChunker
from src.orchestrator.history import append_assistant, append_user
from src.stt.noise import noise_reason


class IgnoredTranscript(Exception):
    """La transcription est vide, du bruit ou une hallucination connue : aucun tour LLM, retour à l'écoute."""

    def __init__(self, reason: str, text: str) -> None:
        super().__init__(reason)
        self.reason, self.text = reason, text


@dataclass
class TurnResult:
    user_text: str
    answer: str
    audio: list[list[bytes]]  # PCM16 24 kHz, un élément par morceau de texte, dans l'ordre de lecture
    stt_ms: float | None
    first_audio_ms: float  # début du tour -> premier chunk PCM du 1er morceau
    total_ms: float
    tts_failed: bool = False  # au moins un morceau n'a pas pu être synthétisé (réponse affichée à l'écrit seulement)


async def run_turn(*, stt: STT, llm: LLM, tts: TTS, history: list[dict], system_prompt: str, rec: Recorder,
                   user_text: str | None = None, audio: tuple[bytes, int] | None = None,
                   first_min: int = 8, rest_min: int = 20,
                   on_user_text: Callable[[str], Awaitable[None]] | None = None,
                   on_chunk: Callable[[str], Awaitable[None]] | None = None,
                   on_audio: Callable[[bytes, int], Awaitable[None]] | None = None,
                   on_search: Callable[[str, str], Awaitable[None]] | None = None,
                   stt_task: asyncio.Task[str] | None = None, ignore_phrases: Sequence[str] = ()) -> TurnResult:
    """on_chunk(texte) : un morceau part vers le TTS ; on_audio(pcm, idx) : audio prêt, dans l'ordre de lecture (idx = rang du morceau) ;
    on_search(requête, annonce) : le LLM cherche sur internet (avant tout morceau de réponse) ; annonce = phrase à dire pendant la recherche ;
    stt_task : transcription déjà lancée sur ce même audio (STT spéculatif) ; repli sur un appel normal si elle a échoué ;
    ignore_phrases : hallucinations à ignorer (avec le vide et le bruit) : `IgnoredTranscript` est levée avant tout appel au LLM."""
    t0 = now_ns()
    stt_ms = None
    if user_text is None:
        if audio is None:
            raise ValueError("user_text ou audio requis")
        try:
            user_text = await stt_task if stt_task else await stt.transcribe(*audio)
        except asyncio.CancelledError:
            raise
        except Exception:  # la transcription spéculative a échoué : un appel normal (avec repli) reste possible
            if stt_task is None:
                raise
            user_text = await stt.transcribe(*audio)
        stt_ms = elapsed_ms(t0)
        if reason := noise_reason(user_text, ignore_phrases):
            raise IgnoredTranscript(reason, user_text)
    if on_user_text:
        await on_user_text(user_text)
    append_user(history, user_text)

    first_audio: list[float] = []
    tts_errors: list[str] = []
    ordered: asyncio.Queue[tuple[int, asyncio.Queue] | None] = asyncio.Queue()  # une file par morceau, drainées dans l'ordre

    async def synth(text: str, q: asyncio.Queue, is_first: bool) -> list[bytes]:
        parts: list[bytes] = []
        try:
            async for pcm in tts.stream(text):
                if is_first and not first_audio:
                    first_audio.append(elapsed_ms(t0))
                    rec("turn_first_audio", ms=round(first_audio[0], 1))
                parts.append(pcm)
                q.put_nowait(pcm)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001  TTS (secours compris) en panne : le texte reste affiché, le tour continue
            tts_errors.append(f"{type(e).__name__}: {e}"[:200])
            rec("tts_error", error=tts_errors[-1])
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
    chunker, tasks, answer, cut_by_length = SentenceChunker(first_min, rest_min), [], "", False

    async def dispatch(text: str) -> None:
        q: asyncio.Queue = asyncio.Queue()
        ordered.put_nowait((len(tasks), q))
        tasks.append(asyncio.create_task(synth(text, q, not tasks)))
        if on_chunk:
            await on_chunk(text)

    try:
        async for delta in llm.stream([{"role": "system", "content": system_prompt}] + history):
            if isinstance(delta, SearchNotice):
                if on_search:
                    await on_search(delta.query, delta.announce)
                continue
            if isinstance(delta, LengthCut):  # réponse coupée par `llm.max_tokens` : l'historique ne gardera que les phrases entières
                cut_by_length = True
                continue
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
    append_assistant(history, answer, cut_by_length)
    total = elapsed_ms(t0)
    rec("turn_done", ms=round(total, 1), chunks=len(tasks))
    return TurnResult(user_text, answer, list(audio_parts), stt_ms, first_audio[0] if first_audio else total, total,
                      tts_failed=bool(tts_errors))
