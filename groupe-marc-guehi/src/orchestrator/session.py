"""Une conversation en duplex : micro -> VAD -> fin de tour -> [accroche] -> run_turn -> audio vers le client.
L'utilisateur peut interrompre l'agent (barge-in) : annulation LLM/TTS, arrêt de la lecture côté client,
troncature de l'historique au texte réellement entendu."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from src.audio.pcm import SAMPLE_RATE_OUT, duration_s, silence
from src.interfaces import LLM, STT, TTS
from src.metrics.recorder import Recorder
from src.orchestrator.truncation import heard_text, history_content
from src.orchestrator.turn import run_turn
from src.tts.fillers import FillerBank
from src.vad.endpointer import Endpointer
from src.vad.silero import FRAME_BYTES, SileroVAD

LISTENING, THINKING, SPEAKING = "listening", "thinking", "speaking"
FILLER_HINT = (" Tu viens de dire à voix haute « {} » : enchaîne directement avec la réponse, "
               "sans nouvelle formule d'attente ni salutation.")


class ConversationSession:
    def __init__(self, *, stt: STT, llm: LLM, tts: TTS, vad: SileroVAD, endpointer: Endpointer, rec: Recorder,
                 system_prompt: str, send_json: Callable[[dict], Awaitable[None]],
                 send_audio: Callable[[bytes], Awaitable[None]], fillers: FillerBank | None = None,
                 filler_min_speech_ms: int = 1800, first_min: int = 8, rest_min: int = 20, max_history: int = 20,
                 barge_min_ms: int = 300, barge_prob: float = 0.75, strict_after_s: float = 0.5,
                 search_fillers: FillerBank | None = None, speculative_ms: int = 0, text_fallback_after: int = 0,
                 wait_fillers: FillerBank | None = None, search_wait_after_s: float = 0.0) -> None:
        self.stt, self.llm, self.tts, self.vad, self.ep, self.rec = stt, llm, tts, vad, endpointer, rec
        self.system_prompt, self.send_json, self.send_audio = system_prompt, send_json, send_audio
        self.fillers, self.filler_min_speech_ms = fillers, filler_min_speech_ms
        self.first_min, self.rest_min, self.max_history = first_min, rest_min, max_history
        self.barge_min_ms, self.barge_prob, self.strict_after_s = barge_min_ms, barge_prob, strict_after_s
        self.search_fillers = search_fillers
        self.wait_fillers, self.search_wait_after_s = wait_fillers, search_wait_after_s  # 2e phrase si la recherche traîne
        self.speculative_ms, self.text_fallback_after = speculative_ms, text_fallback_after
        self.history: list[dict] = []
        self.state = LISTENING
        self.text_only = False                    # bascule automatique : le STT ne répond plus, on n'écoute plus le micro
        self._stt_fails = 0
        self._spec: asyncio.Task | None = None    # transcription lancée avant la fin de tour
        self._buf = b""
        self._strict_until = 0.0
        self._task: asyncio.Task | None = None
        self._failsafe: asyncio.Task | None = None
        self._aux: list[asyncio.Task] = []        # annonce de recherche et phrase d'attente en cours (annulées avec le tour)
        self._carry: bytes | None = None          # audio d'un tour interrompu avant d'être transcrit
        self._reset_response()
        self._interrupt: dict | None = None       # instantané pour affiner la troncature avec le retour du client
        self.vad.reset()

    def _reset_response(self) -> None:
        self._texts: list[str] = []               # morceaux de la réponse (accroche comprise), dans l'ordre de lecture
        self._durs: list[float] = []              # secondes d'audio envoyées par morceau
        self._off = 0                             # 1 si une accroche occupe le rang 0
        self._first_audio_at: float | None = None
        self._turn_complete = False
        self._user_recorded = False
        self._current_audio: tuple[bytes, int] | None = None

    @property
    def agent_active(self) -> bool:
        return self.state in (THINKING, SPEAKING)

    async def _set_state(self, state: str) -> None:
        self.state = state
        await self.send_json({"type": "state", "state": state})

    # ---------- entrées ----------
    async def on_mic(self, data: bytes) -> None:
        """Octets PCM16 16 kHz mono. Le VAD tourne en permanence ; pendant que l'agent réfléchit ou parle, seuil relevé
        et durée minimale plus longue pour ne pas se couper sur son propre écho."""
        if self.text_only:
            return
        self._buf += data
        while len(self._buf) >= FRAME_BYTES:
            frame, self._buf = self._buf[:FRAME_BYTES], self._buf[FRAME_BYTES:]
            prob = self.vad.prob(frame)
            strict = self.agent_active or (time.monotonic() < self._strict_until and not self.ep.in_speech)
            if strict and prob < self.barge_prob:
                prob = 0.0
            for ev in self.ep.push(frame, prob):
                if ev.kind == "speech_start":
                    self.rec("vad_speech_start", agent_active=self.agent_active)
                    await self.send_json({"type": "vad", "speaking": True})
                else:
                    await self.send_json({"type": "vad", "speaking": False})
                    if ev.kind == "speech_end" and self.state == LISTENING:
                        self.rec("vad_speech_end", speech_ms=ev.speech_ms)
                        self._start_turn(audio=(ev.pcm, 16000), speech_ms=ev.speech_ms)
                        return
            self._speculate()
            if self.agent_active and self.ep.in_speech and self.ep.voiced_ms >= self.barge_min_ms:
                await self._barge_in()

    def _speculate(self) -> None:
        """STT spéculatif : on transcrit dès `speculative_ms` de silence, sans attendre la fin de tour. Si la parole reprend,
        le résultat est jeté ; sinon il est déjà (en partie) prêt quand la fin de tour est déclarée."""
        if not self.speculative_ms:
            return
        silence = self.ep.silence_ms
        if self._spec and (not self.ep.in_speech or silence < self.speculative_ms):
            self._spec.cancel()
            self._spec = None
        if self._spec is None and self.ep.in_speech and not self.agent_active and silence >= self.speculative_ms:
            self.rec("stt_speculative_start", silence_ms=silence)
            self._spec = asyncio.create_task(self.stt.transcribe(self.ep.snapshot(), 16000))
            self._spec.add_done_callback(lambda t: t.cancelled() or t.exception())  # erreur lue : sans bruit « never retrieved »

    async def on_voice_retry(self) -> None:
        """Le client demande de réessayer la voix après une bascule automatique en mode texte."""
        self.text_only, self._stt_fails = False, 0
        self.vad.reset()
        self.ep.reset()
        self._buf = b""
        await self.send_json({"type": "fallback", "mode": "voice"})

    async def on_text(self, text: str) -> None:
        """Mode texte de secours : même chaîne, sans micro ni STT (interrompt l'agent s'il parle)."""
        if not text.strip():
            return
        if self.agent_active:
            await self._barge_in()
        self._start_turn(user_text=text.strip())

    async def on_playback_ended(self) -> None:
        if self.state == SPEAKING and self._turn_complete:
            self._strict_until = time.monotonic() + self.strict_after_s
            self.vad.reset()
            self.ep.reset()
            self._buf = b""
            await self._set_state(LISTENING)

    async def on_interrupted(self, played_s: float) -> None:
        """Retour du client : durée réellement jouée au moment de l'arrêt. Affine la troncature de l'historique."""
        snap, self._interrupt = self._interrupt, None
        if not snap:
            return
        self.rec("playback_stopped", played_s=round(played_s, 2),
                 ack_ms=round((time.monotonic() - snap["t"]) * 1000, 1))
        heard, trunc = heard_text(snap["texts"], snap["durs"], played_s, snap["complete"])
        pos = snap["pos"]
        if pos is not None and 0 <= pos < len(self.history) and self.history[pos]["role"] == "assistant" and heard:
            self.history[pos]["content"] = history_content(heard, trunc)
        await self.send_json({"type": "truncate", "text": heard})

    async def close(self) -> None:
        for t in (self._task, self._failsafe, self._spec, *self._aux):
            if t:
                t.cancel()

    # ---------- interruption ----------
    async def _barge_in(self) -> None:
        t = time.monotonic()
        self.rec("barge_in_detected", voiced_ms=self.ep.voiced_ms, state=self.state)
        for task in (self._task, self._failsafe, *self._aux):
            if task:
                task.cancel()
        if self._task:
            await asyncio.gather(self._task, *self._aux, return_exceptions=True)
        self._aux = []
        if not self._user_recorded and self._current_audio:
            self._carry = self._current_audio[0]  # la phrase pas encore transcrite sera reprise avec la suivante
        played = max(0.0, t - self._first_audio_at - 0.05) if self._first_audio_at else 0.0
        snap = {"texts": list(self._texts), "durs": list(self._durs), "complete": self._turn_complete, "pos": None, "t": t}
        heard, trunc = heard_text(snap["texts"], snap["durs"], played, snap["complete"])
        if snap["complete"] and self.history and self.history[-1]["role"] == "assistant":
            if heard:
                self.history[-1]["content"] = history_content(heard, trunc)
                snap["pos"] = len(self.history) - 1
            else:
                self.history.pop()
        elif heard:
            self.history.append({"role": "assistant", "content": history_content(heard, trunc)})
            snap["pos"] = len(self.history) - 1
        self._interrupt = snap
        self._reset_response()
        self._strict_until = 0.0
        self.state = LISTENING  # l'endpointer garde son état : la phrase de l'utilisateur est déjà en cours de capture
        await self.send_json({"type": "interrupt"})
        await self.send_json({"type": "truncate", "text": heard})
        await self.send_json({"type": "state", "state": LISTENING})

    # ---------- tour ----------
    def _start_turn(self, *, audio: tuple[bytes, int] | None = None, user_text: str | None = None,
                    speech_ms: int = 0) -> None:
        self._buf = b""
        self.ep.reset()
        if self._failsafe:
            self._failsafe.cancel()
        spec, self._spec = self._spec, None
        if audio and self._carry:  # audio repris d'un tour interrompu : la transcription spéculative ne couvre pas tout
            audio = (self._carry + silence(150, 16000) + audio[0], 16000)
            if spec:
                spec.cancel()
            spec = None
        elif spec and audio is None:
            spec.cancel()
            spec = None
        self._carry = None
        self._aux = []
        self._reset_response()
        self._current_audio = audio
        self._task = asyncio.create_task(self._turn(audio, user_text, speech_ms, spec))

    async def _to_listening(self) -> None:
        self._strict_until = time.monotonic() + self.strict_after_s
        self.vad.reset()
        self.ep.reset()
        self._buf = b""
        await self._set_state(LISTENING)

    async def _turn(self, audio, user_text, speech_ms, stt_task=None) -> None:
        self.rec.turn += 1
        n0 = len(self.rec.events)
        t_detect = time.perf_counter()
        first_content: list[float] = []
        filler = None
        spoken: list[str] = []                    # accroches dites avant la réponse (pour l'historique)
        lead: list[float] = []                    # [1er son de l'attente, fin estimée de l'attente], en s depuis la détection
        searched = False
        announces: list[asyncio.Task] = []        # annonce de recherche (synthétisée en parallèle de la recherche)
        await self._set_state(THINKING)
        if audio is not None:
            await self.send_json({"type": "user_pending"})
            if self.fillers and speech_ms >= self.filler_min_speech_ms:
                filler = self.fillers.pick()
                spoken.append(filler.text)
                lead[:] = [0.0, filler.dur]
                self._texts.append(filler.text)
                self._durs.append(filler.dur)
                self._off = 1
                self.rec("filler_sent", text=filler.text, dur_s=round(filler.dur, 2))
                await self.send_json({"type": "agent_chunk", "text": filler.text})
                self._first_audio_at = time.monotonic()
                await self.send_audio(filler.pcm)
                await self._set_state(SPEAKING)

        async def on_user(text: str) -> None:
            self._user_recorded = True
            self._stt_fails = 0
            await self.send_json({"type": "transcript", "role": "user", "text": text})

        async def on_chunk(text: str) -> None:
            self._texts.append(text)
            self._durs.append(0.0)
            await self.send_json({"type": "agent_chunk", "text": text})

        async def on_audio(pcm: bytes, idx: int) -> None:
            self._durs[idx + self._off] += duration_s(pcm, SAMPLE_RATE_OUT)
            if not first_content:
                await asyncio.gather(*announces, return_exceptions=True)  # l'annonce de recherche finit d'abord (ordre de lecture)
                first_content.append(time.perf_counter() - t_detect)
                if self._first_audio_at is None:
                    self._first_audio_at = time.monotonic()
                if self.state != SPEAKING:
                    await self._set_state(SPEAKING)
            await self.send_audio(pcm)

        def reserve(text: str, dur: float, kind: str) -> int:
            """Réserve le rang d'une phrase d'attente : sans appel await, donc avant toute réponse (les rangs ne se décalent pas)."""
            self._texts.append(text)
            self._durs.append(dur)
            self._off += 1
            spoken.append(text)
            self.rec("filler_sent", text=text, dur_s=round(dur, 2), kind=kind)
            return len(self._texts) - 1

        def waiting(start: float, dur: float) -> None:
            """Met à jour l'attente couverte par l'audio de remplissage : 1er son, et fin de lecture estimée."""
            lead[:] = [lead[0] if lead else start, max(lead[1] if lead else 0.0, start) + dur]

        async def play_filler(f, kind: str) -> None:
            now = time.perf_counter() - t_detect
            reserve(f.text, f.dur, kind)
            waiting(now, f.dur)
            await self.send_json({"type": "agent_chunk", "text": f.text})
            if self._first_audio_at is None:
                self._first_audio_at = time.monotonic()
            await self.send_audio(f.pcm)
            if self.state != SPEAKING:
                await self._set_state(SPEAKING)

        async def announce_search(text: str) -> None:
            """L'agent dit ce qu'il cherche (phrase écrite par le LLM, synthétisée pendant que la recherche s'exécute).
            Sans audio (TTS en panne), on retombe sur l'accroche générique pré-enregistrée."""
            idx = reserve(text, 0.0, "search_announce")
            start, sent = None, False
            try:
                async for pcm in self.tts.stream(text):
                    if not sent:
                        sent = True
                        start = time.perf_counter() - t_detect
                        await self.send_json({"type": "agent_chunk", "text": text})
                        if self._first_audio_at is None:
                            self._first_audio_at = time.monotonic()
                    self._durs[idx] += duration_s(pcm, SAMPLE_RATE_OUT)
                    await self.send_audio(pcm)
                    if self.state != SPEAKING:
                        await self._set_state(SPEAKING)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001  l'annonce est un confort : la conversation continue sans
                self.rec("tts_error", error=f"{type(e).__name__}: {e}"[:200], kind="search_announce")
            if sent:
                waiting(start, self._durs[idx])
                return
            spoken.remove(text)  # rien n'a été entendu : ni dans l'historique ni dans la troncature
            self._texts[idx] = ""
            if self.search_fillers is not None:
                f = self.search_fillers.pick()
                self._texts[idx], self._durs[idx] = f.text, f.dur
                spoken.append(f.text)
                waiting(time.perf_counter() - t_detect, f.dur)
                await self.send_json({"type": "agent_chunk", "text": f.text})
                if self._first_audio_at is None:
                    self._first_audio_at = time.monotonic()
                await self.send_audio(f.pcm)
                if self.state != SPEAKING:
                    await self._set_state(SPEAKING)

        async def wait_more() -> None:
            """La recherche traîne : une 2e phrase brève évite un long silence, sauf si la réponse est déjà en route."""
            await asyncio.sleep(self.search_wait_after_s)
            await asyncio.gather(*announces, return_exceptions=True)  # après l'annonce, pour respecter l'ordre de lecture
            if len(self._texts) == self._off and not first_content:
                await play_filler(self.wait_fillers.pick(), "search_wait")

        async def on_search(query: str, announce: str = "") -> None:
            """Le LLM cherche sur internet : l'agent dit ce qu'il cherche (ou une accroche générique) pour couvrir l'attente, sauf
            s'il a déjà commencé sa réponse (les rangs des morceaux ne doivent pas se décaler)."""
            nonlocal searched
            searched = True
            self.rec("search_notice", chars=len(query), announced=bool(announce))
            await self.send_json({"type": "searching"})
            if len(self._texts) > self._off:
                return
            if announce:
                announces.append(asyncio.create_task(announce_search(announce)))  # en tâche : ne retarde pas la recherche
                self._aux.append(announces[-1])
            elif self.search_fillers is not None:
                await play_filler(self.search_fillers.pick(), "search")
            if self.wait_fillers is not None and self.search_wait_after_s > 0:
                self._aux.append(asyncio.create_task(wait_more()))

        prompt = self.system_prompt + (FILLER_HINT.format(filler.text) if filler else "")
        try:
            r = await run_turn(stt=self.stt, llm=self.llm, tts=self.tts, history=self.history, system_prompt=prompt,
                               rec=self.rec, user_text=user_text, audio=audio, first_min=self.first_min,
                               rest_min=self.rest_min, on_user_text=on_user, on_chunk=on_chunk, on_audio=on_audio,
                               on_search=on_search, stt_task=stt_task)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001  toute erreur de fournisseur doit rester visible sans tuer la session
            for t in self._aux:
                t.cancel()
            self.rec("turn_error", error=f"{type(e).__name__}: {e}"[:300])
            if self.history and self.history[-1]["role"] == "user":
                self.history.pop()
            await self.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"[:300],
                                  "hint": "Vous pouvez continuer à l'écrit (mode texte)."})
            stt_failed = audio is not None and not self._user_recorded
            self._reset_response()
            await self._to_listening()
            if stt_failed:
                self._stt_fails += 1
                if self.text_fallback_after and self._stt_fails >= self.text_fallback_after:
                    self.text_only = True  # le micro n'est plus écouté tant que le client ne redemande pas la voix
                    self.rec("fallback_text_mode", failures=self._stt_fails)
                    await self.send_json({"type": "fallback", "mode": "text", "reason": "stt"})
            return
        await asyncio.gather(*announces, return_exceptions=True)
        for t in self._aux:  # la phrase « encore un instant » ne doit plus partir une fois la réponse donnée
            t.cancel()
        if r.tts_failed:
            await self.send_json({"type": "tts_down"})
        self._turn_complete = True
        if spoken:
            self.history[-1]["content"] = " ".join(spoken + [self.history[-1]["content"]])
        del self.history[:-self.max_history]
        end_sil = 0 if audio is None else self.ep.cfg.end_silence_ms
        fc = first_content[0] if first_content else None
        if filler and fc is not None and not searched:  # l'attente d'une recherche fausserait l'attente moyenne
            self.fillers.observe(fc)
        if fc is None:
            first_sound = content = None
        elif lead:  # 1er son = l'accroche ; la réponse ne démarre qu'une fois l'attente (accroches) terminée
            first_sound, content = end_sil + lead[0] * 1000, end_sil + max(fc, lead[1]) * 1000
        else:
            first_sound = content = end_sil + fc * 1000
        await self.send_json({
            "type": "metrics", "turn": self.rec.turn, "stt_ms": r.stt_ms, "end_silence_ms": end_sil,
            "llm_ttft_ms": self._ms("llm_first_token", n0), "tts_ttfb_ms": self._ms("tts_first_pcm", n0),
            "first_sound_ms": first_sound, "content_ms": content, "total_ms": r.total_ms,
            "filler": " ".join(spoken) or None, "search_ms": self._ms("search_done", n0), "searched": searched,
            "gap_ms": max(0.0, (fc - lead[1]) * 1000) if lead and fc is not None else 0.0})
        await self.send_json({"type": "audio_end"})
        if self._first_audio_at is not None:
            # filet de sécurité si le client ne confirme jamais la fin de lecture
            self._failsafe = asyncio.create_task(self._failsafe_listen(sum(self._durs) + 5))
        else:
            await self._to_listening()

    def _ms(self, event: str, start: int) -> float | None:
        vals = [d.get("ms") for e, _, d in self.rec.events[start:] if e == event and d.get("ms") is not None]
        return vals[0] if vals else None

    async def _failsafe_listen(self, delay: float) -> None:
        await asyncio.sleep(delay)
        if self.state == SPEAKING:
            await self.on_playback_ended()
