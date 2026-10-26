"""Un tour complet côté session : [accroche] -> run_turn (STT, LLM, TTS) -> historique, métriques, fin d'audio."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from src.audio.pcm import SAMPLE_RATE_OUT, duration_s
from src.metrics.clock import elapsed_s, now_ns
from src.orchestrator.history import History
from src.orchestrator.response import ResponseState
from src.orchestrator.session_config import SessionConfig, SessionDeps
from src.orchestrator.state import State, StateMachine
from src.orchestrator.tasks import TurnTasks
from src.orchestrator.turn import IgnoredTranscript, run_turn
from src.orchestrator.turn_metrics import turn_metrics
from src.orchestrator.waiting import WaitingAudio


@dataclass
class TurnOutcome:
    error: bool = False
    stt_failed: bool = False   # l'erreur vient de la transcription (compte pour la bascule en mode texte)
    playing: bool = False      # de l'audio a été envoyé : on attend l'accusé de fin de lecture du client


class TurnRunner:
    def __init__(self, deps: SessionDeps, cfg: SessionConfig, resp: ResponseState, history: History, tasks: TurnTasks,
                 sm: StateMachine) -> None:
        self.deps, self.cfg, self.resp, self.history, self.tasks, self.sm = deps, cfg, resp, history, tasks, sm

    async def run(self, audio: tuple[bytes, int] | None, user_text: str | None, speech_ms: int,
                  stt_task: asyncio.Task | None, on_user_recorded: Callable[[], None]) -> TurnOutcome:
        d, resp = self.deps, self.resp
        d.rec.turn += 1
        n0 = len(d.rec.events)
        resp.t_detect = now_ns()
        waiting = WaitingAudio(d, self.cfg, resp, self.tasks, self.sm)
        await d.send_json({"type": "state", "state": State.THINKING.value})  # l'état a déjà changé (`_start_turn`)
        filler = None
        if audio is not None:
            await d.send_json({"type": "user_pending"})
            if d.fillers and speech_ms >= self.cfg.filler_min_speech_ms:
                filler = d.fillers.pick()
                await waiting.play_opening(filler)

        async def on_user(text: str) -> None:
            resp.user_recorded = True
            on_user_recorded()
            await d.send_json({"type": "transcript", "role": "user", "text": text})

        shown = 0  # rang du prochain morceau à envoyer au client (les phrases d'attente partent seules, avec leur audio)

        async def show_upto(rank: int) -> None:
            """Envoie le texte des morceaux jusqu'au rang `rank` : il part avec l'audio, pas avant (affichage au rythme de la voix)."""
            nonlocal shown
            shown = max(shown, resp.off)  # `off` grandit pendant une recherche : sauter les phrases d'attente déjà envoyées
            while shown <= rank and shown < len(resp.texts):
                shown += 1
                await d.send_json({"type": "agent_chunk", "text": resp.texts[shown - 1]})

        async def on_chunk(text: str) -> None:
            resp.add_chunk(text)

        async def on_audio(pcm: bytes, idx: int) -> None:
            await show_upto(idx + resp.off)
            resp.durs[idx + resp.off] += duration_s(pcm, SAMPLE_RATE_OUT)
            if resp.first_content_s is None:
                await asyncio.gather(*waiting.announces, return_exceptions=True)  # l'annonce de recherche finit d'abord
                resp.first_content_s = elapsed_s(resp.t_detect)
                resp.mark_first_audio()
                await self.sm.ensure_speaking()
            await d.send_audio(pcm)

        prompt = d.system_prompt + (self.cfg.filler_hint.format(filler.text) if filler else "")
        try:
            r = await run_turn(stt=d.stt, llm=d.llm, tts=d.tts, history=self.history.messages, system_prompt=prompt,
                               rec=d.rec, user_text=user_text, audio=audio, first_min=self.cfg.first_min,
                               rest_min=self.cfg.rest_min, on_user_text=on_user, on_chunk=on_chunk, on_audio=on_audio,
                               on_search=waiting.on_search, stt_task=stt_task, ignore_phrases=self.cfg.ignore_phrases)
        except asyncio.CancelledError:
            raise
        except IgnoredTranscript as e:  # rien à répondre : l'accroche déjà jouée se termine, puis retour à l'écoute
            return await self._ignored(e, waiting)
        except Exception as e:  # noqa: BLE001  toute erreur de fournisseur doit rester visible sans tuer la session
            self.tasks.cancel_aux()
            d.rec("turn_error", error=f"{type(e).__name__}: {e}"[:300])
            self.history.drop_pending_user()
            await d.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"[:300],
                               "hint": "Vous pouvez continuer à l'écrit (mode texte)."})
            return TurnOutcome(error=True, stt_failed=audio is not None and not resp.user_recorded)
        await asyncio.gather(*waiting.announces, return_exceptions=True)
        self.tasks.cancel_aux()  # la phrase « encore un instant » ne doit plus partir une fois la réponse donnée
        await show_upto(len(resp.texts))  # morceaux sans audio (voix en panne) : le texte reste affiché
        if r.tts_failed:
            await d.send_json({"type": "tts_down"})
        resp.turn_complete = True
        if resp.spoken:
            self.history.prefix_last(resp.spoken)
        self.history.trim()
        end_sil = 0 if audio is None else d.endpointer.cfg.end_silence_ms
        if filler and resp.first_content_s is not None and not resp.searched:  # l'attente d'une recherche fausserait l'attente moyenne
            d.fillers.observe(resp.first_content_s)
        await d.send_json(turn_metrics(d.rec, n0, r, resp, end_sil))
        await d.send_json({"type": "audio_end"})
        return TurnOutcome(playing=resp.first_audio_at is not None)

    async def _ignored(self, e: IgnoredTranscript, waiting: WaitingAudio) -> TurnOutcome:
        """Transcription vide, bruit ou hallucination : pas de tour LLM, aucun message dans l'historique. Si une accroche a déjà
        été envoyée, on laisse le client la finir (`audio_end`) avant de repasser en écoute."""
        d, resp = self.deps, self.resp
        self.tasks.cancel_aux()
        d.rec("turn_ignored", reason=e.reason, chars=len(e.text))
        await d.send_json({"type": "ignored"})
        resp.turn_complete, resp.ignored = True, True
        playing = resp.first_audio_at is not None
        if playing:
            await d.send_json({"type": "audio_end"})
        return TurnOutcome(playing=playing)
