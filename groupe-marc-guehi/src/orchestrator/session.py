"""Une conversation en duplex : micro -> VAD -> fin de tour -> [accroche] -> run_turn -> audio vers le client.
L'utilisateur peut interrompre l'agent (barge-in) : annulation LLM/TTS, arrêt de la lecture côté client,
troncature de l'historique au texte réellement entendu. Coordinateur mince : le détail est dans capture, turn_runner,
interruption, history, state, tasks et response."""
from __future__ import annotations

import asyncio

from src.audio.pcm import silence
from src.metrics.clock import elapsed_ms, now_ns
from src.orchestrator.capture import MIC_RATE, Capture
from src.orchestrator.history import History
from src.orchestrator.interruption import Interruptions
from src.orchestrator.response import ResponseState
from src.orchestrator.session_config import SessionConfig, SessionDeps
from src.orchestrator.state import State, StateMachine
from src.orchestrator.tasks import TurnTasks
from src.orchestrator.turn_runner import TurnRunner


class ConversationSession:
    def __init__(self, deps: SessionDeps, cfg: SessionConfig) -> None:
        self.deps, self.cfg = deps, cfg
        self.sm = StateMachine(deps.rec, deps.send_json, strict=cfg.strict_transitions)
        self.history = History(cfg.max_history)
        self.resp = ResponseState()
        self.tasks = TurnTasks(deps.rec)
        self.capture = Capture(deps, cfg, self.tasks, lambda: self.sm.agent_active)
        self.interruptions = Interruptions(deps, cfg, self.resp, self.history, self.tasks, self.sm, self.capture)
        self.runner = TurnRunner(deps, cfg, self.resp, self.history, self.tasks, self.sm)
        self.text_only = False                    # bascule automatique : le STT ne répond plus, on n'écoute plus le micro
        self.muted = False                        # micro coupé par l'utilisateur : aucune parole captée, donc aucune interruption
        self._stt_fails = 0
        deps.vad.reset()

    @property
    def state(self) -> State:
        return self.sm.state

    # ---------- entrées ----------
    async def on_mic(self, data: bytes) -> None:
        if self.text_only or self.muted:
            return
        for sig in self.capture.frames(data):
            if sig.kind == "barge_in":
                await self.interruptions.barge_in()
            elif sig.kind == "speech_start":
                self.deps.rec("vad_speech_start", agent_active=self.sm.agent_active)
                await self.deps.send_json({"type": "vad", "speaking": True})
            else:
                await self.deps.send_json({"type": "vad", "speaking": False})
                if sig.kind == "speech_end" and self.state is State.LISTENING:
                    self.deps.rec("vad_speech_end", speech_ms=sig.event.speech_ms)
                    self._start_turn(audio=(sig.event.pcm, MIC_RATE), speech_ms=sig.event.speech_ms)
                    return

    async def on_mute(self, muted: bool) -> None:
        """Coupe ou rétablit l'écoute (ex. endroit bruyant). À la coupure, l'énoncé en cours est abandonné : il ne finirait jamais."""
        if muted == self.muted:
            return
        self.muted = muted
        self.deps.rec("mic_muted" if muted else "mic_unmuted")
        self.capture.reset()
        await self.deps.send_json({"type": "vad", "speaking": False})

    async def on_voice_retry(self) -> None:
        """Le client demande de réessayer la voix après une bascule automatique en mode texte."""
        self.text_only, self._stt_fails = False, 0
        self.capture.reset()
        await self.deps.send_json({"type": "fallback", "mode": "voice"})

    async def on_text(self, text: str) -> None:
        """Mode texte de secours : même chaîne, sans micro ni STT (interrompt l'agent s'il parle)."""
        if not text.strip():
            return
        if self.sm.agent_active:
            await self.interruptions.barge_in()
        self._start_turn(user_text=text.strip())

    async def on_playback_ended(self) -> None:
        if self.state is State.SPEAKING and self.resp.turn_complete:
            self.capture.reset(self.cfg.strict_after_s)
            await self.sm.enter(State.LISTENING)

    def on_playback_started(self, in_ms: float) -> None:
        """Le client a planifié le premier échantillon de la réponse : il sera audible dans `in_ms` ms (latence de sortie comprise).
        Trace `playback_first_sample` (METRICS : E2E = premier échantillon audible − fin de parole détectée). Une seule fois par
        réponse ; ignoré sans audio envoyé (message en retard, après une interruption)."""
        resp = self.resp
        if self.state not in (State.THINKING, State.SPEAKING) or resp.first_audio_ns is None or resp.audible_ns is not None:
            return
        resp.audible_ns = now_ns() + int(in_ms * 1e6)
        self.deps.rec("playback_first_sample", e2e_ms=round(elapsed_ms(resp.t_detect, resp.audible_ns), 1),
                      send_to_audible_ms=round(elapsed_ms(resp.first_audio_ns, resp.audible_ns), 1), client_lead_ms=round(in_ms, 1))

    async def on_interrupted(self, played_s: float) -> None:
        await self.interruptions.on_client_ack(played_s)

    async def close(self) -> None:
        await self.tasks.cancel_all()

    # ---------- tour ----------
    def _start_turn(self, *, audio: tuple[bytes, int] | None = None, user_text: str | None = None,
                    speech_ms: int = 0) -> None:
        self.capture.clear()
        self.tasks.cancel_failsafe()
        spec = self.tasks.take_spec()
        carry = self.interruptions.take_carry()
        if audio and carry:  # audio repris d'un tour interrompu : la transcription spéculative ne couvre pas tout
            audio = (carry + silence(self.cfg.carry_gap_ms, MIC_RATE) + audio[0], MIC_RATE)
            drop_spec = True
        else:
            drop_spec = audio is None
        if spec and drop_spec:
            spec.cancel()
            spec = None
        self.resp.reset()
        self.resp.current_audio = audio
        self.sm.to(State.THINKING)
        self.tasks.spawn_turn(self._run_turn(audio, user_text, speech_ms, spec))

    async def _run_turn(self, audio, user_text, speech_ms, spec) -> None:
        out = await self.runner.run(audio, user_text, speech_ms, spec, on_user_recorded=self._user_recorded)
        if out.playing and not out.error:
            # filet de sécurité si le client ne confirme jamais la fin de lecture
            self.tasks.spawn_failsafe(self._failsafe_listen(self.resp.audio_s + self.cfg.failsafe_margin_s))
            return
        if out.error:
            self.resp.reset()
        await self._to_listening()
        if out.stt_failed:
            self._stt_fails += 1
            if self.cfg.text_fallback_after and self._stt_fails >= self.cfg.text_fallback_after:
                self.text_only = True  # le micro n'est plus écouté tant que le client ne redemande pas la voix
                self.deps.rec("fallback_text_mode", failures=self._stt_fails)
                await self.deps.send_json({"type": "fallback", "mode": "text", "reason": "stt"})

    def _user_recorded(self) -> None:
        self._stt_fails = 0

    async def _to_listening(self) -> None:
        self.capture.reset(self.cfg.strict_after_s)
        await self.sm.enter(State.LISTENING)

    async def _failsafe_listen(self, delay: float) -> None:
        await asyncio.sleep(delay)
        if self.state is State.SPEAKING:
            await self.on_playback_ended()
