"""One turn owns its tasks and queues; interruption destroys that ownership."""

import asyncio
import time
from collections.abc import Callable
from contextlib import aclosing

import numpy as np

from voiceagent.config import Config
from voiceagent.domain import AudioChunk, LanguageModel, Player, State, Synthesizer, Transcriber
from voiceagent.history import History
from voiceagent.sentences import SentenceBuffer
from voiceagent.workers import SerialWorker


class Agent:
    def __init__(
        self,
        config: Config,
        events,
        stt: Transcriber | None,
        llm: LanguageModel,
        tts: Synthesizer | None,
        player: Player,
        on_token: Callable[[str], None] | None = None,
    ):
        self.config = config
        self.events = events
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self.player = player
        self.on_token = on_token or (lambda token: None)
        self.history = History(config.llm.system_prompt, config.llm.history_turns)
        self.state = State.LISTENING
        self.turn = 0
        self._task: asyncio.Task | None = None
        self._stt_worker = SerialWorker("stt")
        self._tts_worker = SerialWorker("tts")

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def set_state(self, state: State) -> None:
        self.state = state
        self.events.emit("state", self.turn, state=state.value)

    def begin_speech(self, start_ns: int, probability: float) -> int:
        self.turn += 1
        self.set_state(State.USER_SPEAKING)
        self.events.emit("speech_start", self.turn, t_ns=start_ns, probability=probability)
        return self.turn

    async def submit_text(self, text: str) -> int:
        if self.busy:
            await self.interrupt(reason="text")
        self.turn += 1
        self.events.emit("text_input", self.turn, text=text)
        self._task = asyncio.create_task(self._respond(self.turn, text=text))
        return self.turn

    def submit_audio(self, utterance, **metadata) -> int:
        if self.busy:
            raise RuntimeError("Annuler la réponse précédente avant d'envoyer un nouveau tour")
        self.events.emit("speech_end_real", self.turn, t_ns=utterance.end_ns)
        self.events.emit(
            "vad_endpoint",
            self.turn,
            t_ns=utterance.endpoint_ns,
            duration_ms=len(utterance.samples) / 16,
            **metadata,
        )
        self._task = asyncio.create_task(self._respond(self.turn, utterance=utterance))
        return self.turn

    async def wait_idle(self) -> None:
        if self._task:
            await self._task

    async def interrupt(self, reason: str = "speech", **data) -> float | None:
        if not self.busy:
            return
        turn = self.turn
        detected_ns = time.perf_counter_ns()
        self.events.emit("barge_in_detected", turn, t_ns=detected_ns, reason=reason, **data)
        # Stop output BEFORE waiting for network/model cancellation.
        stopped_ns = None
        try:
            stopped_ns = await self.player.stop()
            self.events.emit(
                "playback_stopped",
                turn,
                t_ns=stopped_ns,
                clock="host_abort_ack",
                stop_ms=(stopped_ns - detected_ns) / 1e6,
            )
        except Exception as exc:
            self.events.emit("error", turn, stage="playback_stop", exception=repr(exc))
        finally:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self.events.emit("barge_in", turn, detected_ns=detected_ns, stopped_ns=stopped_ns)
        return None if stopped_ns is None else (stopped_ns - detected_ns) / 1e6

    async def _respond(self, turn: int, text: str = "", utterance=None) -> None:
        heard: list[str] = []
        interrupted = False
        self.set_state(State.THINKING)
        stage = "stt" if utterance is not None else "pipeline"
        try:
            async with asyncio.timeout(self.config.turn_timeout_s):
                if utterance is not None:
                    if self.stt is None:
                        raise RuntimeError("Moteur STT indisponible")
                    text = await self._stt_worker.run(self.stt.transcribe, utterance.samples)
                    self.events.emit(
                        "stt_done",
                        turn,
                        text=text,
                        language="fr",
                        audio_duration_s=len(utterance.samples) / 16000,
                    )
                if not text.strip():
                    self.events.emit("empty_transcript", turn)
                    return
                stage = "pipeline"
                await self._pipeline(turn, text, heard)
                self.events.emit("turn_done", turn)
        except asyncio.CancelledError:
            interrupted = True
            self.events.emit("turn_cancelled", turn)
            raise
        except Exception as exc:
            interrupted = True
            await self.player.stop()
            self.events.emit("error", turn, stage=stage, exception=repr(exc))
        finally:
            if text.strip():
                answer = self.history.commit(text, heard, interrupted)
                self.events.emit(
                    "history_commit",
                    turn,
                    user=text,
                    assistant=answer,
                    granularity="completed_synthesis_units",
                    interrupted=interrupted,
                )
            self.set_state(State.LISTENING)

    async def _pipeline(self, turn: int, text: str, heard: list[str]) -> None:
        sentences: asyncio.Queue[str | None] = asyncio.Queue(maxsize=4)
        audio: asyncio.Queue[AudioChunk | None] = asyncio.Queue(maxsize=3)
        async with asyncio.TaskGroup() as group:
            group.create_task(
                self._stage("llm", turn, self._produce_sentences(turn, text, sentences))
            )
            group.create_task(
                self._stage("tts", turn, self._synthesize(turn, sentences, audio))
            )
            group.create_task(self._stage("playback", turn, self._play(turn, audio, heard)))

    async def _stage(self, name: str, turn: int, operation) -> None:
        try:
            await operation
        except Exception as exc:
            self.events.emit("error", turn, stage=name, exception=repr(exc))
            raise

    async def _produce_sentences(self, turn: int, text: str, queue) -> None:
        buffer = SentenceBuffer(self.config.llm.max_sentence_chars)
        chunks = 0
        first_sentence = True
        async with aclosing(self.llm.stream(self.history.messages(text))) as stream:
            async for token in stream:
                if not token:
                    continue
                if chunks == 0:
                    self.events.emit("llm_first_token", turn)
                chunks += 1
                self.on_token(token)
                for sentence in buffer.feed(token):
                    if first_sentence:
                        self.events.emit("llm_first_sentence", turn, sentence=sentence)
                        first_sentence = False
                    await queue.put(sentence)
        for sentence in buffer.flush():
            if first_sentence:
                self.events.emit("llm_first_sentence", turn, sentence=sentence)
                first_sentence = False
            await queue.put(sentence)
        # NDJSON fragments are not tokenizer tokens: name the count accurately.
        self.events.emit(
            "llm_done",
            turn,
            stream_chunks=chunks,
            tokens=getattr(self.llm, "last_eval_count", None),
        )
        await queue.put(None)

    async def _synthesize(self, turn: int, sentences, audio) -> None:
        first = True
        while True:
            sentence = await sentences.get()
            if sentence is None:
                await audio.put(None)
                return
            if self.tts is None:
                chunk = AudioChunk(sentence, np.empty(0, dtype=np.float32), 24000)
            else:
                chunk = await self._tts_worker.run(self.tts.synthesize, sentence)
                if first:
                    self.events.emit("tts_first_chunk", turn)
                    first = False
            await audio.put(chunk)

    async def _play(self, turn: int, audio, heard: list[str]) -> None:
        while True:
            chunk = await audio.get()
            if chunk is None:
                if self.player.audible:
                    self.events.emit("playback_end", turn, text=" ".join(heard))
                return
            if self.player.audible:
                self.set_state(State.SPEAKING)
            if await self.player.play(chunk, turn):
                heard.append(chunk.text)

    async def close(self) -> None:
        if self.busy:
            await self.interrupt(reason="shutdown")
        try:
            await self.player.close()
        finally:
            try:
                close = getattr(self.llm, "close", None)
                if close:
                    await close()
            finally:
                self._stt_worker.close()
                self._tts_worker.close()
