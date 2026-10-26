"""Remplissage de l'attente : accroche d'ouverture, annonce de recherche (dite par l'agent), 2e phrase si la recherche traîne."""
from __future__ import annotations

import asyncio

from src.audio.pcm import SAMPLE_RATE_OUT, duration_s
from src.metrics.clock import elapsed_s
from src.orchestrator.response import ResponseState
from src.orchestrator.session_config import SessionConfig, SessionDeps
from src.orchestrator.state import StateMachine
from src.orchestrator.tasks import TurnTasks
from src.tts.fillers import Filler


class WaitingAudio:
    def __init__(self, deps: SessionDeps, cfg: SessionConfig, resp: ResponseState, tasks: TurnTasks, sm: StateMachine) -> None:
        self.deps, self.cfg, self.resp, self.tasks, self.sm = deps, cfg, resp, tasks, sm
        self.announces: list[asyncio.Task] = []    # annonces de recherche (synthétisées en parallèle de la recherche)

    def _since_detect(self) -> float:
        return elapsed_s(self.resp.t_detect)

    def _record(self, f_text: str, dur: float, kind: str | None) -> int:
        idx = self.resp.reserve(f_text, dur)
        self.deps.rec("filler_sent", text=f_text, dur_s=round(dur, 2), **({"kind": kind} if kind else {}))
        return idx

    async def _emit(self, f: Filler) -> None:
        await self.deps.send_json({"type": "agent_chunk", "text": f.text})
        self.resp.mark_first_audio()
        await self.deps.send_audio(f.pcm)
        await self.sm.ensure_speaking()

    async def play_opening(self, f: Filler) -> None:
        """Accroche neutre jouée dès la fin de tour, avant de connaître la réponse."""
        self._record(f.text, f.dur, None)
        self.resp.waiting(0.0, f.dur)
        await self._emit(f)

    async def play_filler(self, f: Filler, kind: str) -> None:
        self._record(f.text, f.dur, kind)
        self.resp.waiting(self._since_detect(), f.dur)
        await self._emit(f)

    async def announce_search(self, text: str) -> None:
        """L'agent dit ce qu'il cherche (phrase écrite par le LLM, synthétisée pendant que la recherche s'exécute).
        Sans audio (TTS en panne), on retombe sur l'accroche générique pré-enregistrée."""
        r, d = self.resp, self.deps
        idx = self._record(text, 0.0, "search_announce")
        start, sent = 0.0, False
        try:
            async for pcm in d.tts.stream(text):
                if not sent:
                    sent, start = True, self._since_detect()
                    await d.send_json({"type": "agent_chunk", "text": text})
                    r.mark_first_audio()
                r.durs[idx] += duration_s(pcm, SAMPLE_RATE_OUT)
                await d.send_audio(pcm)
                await self.sm.ensure_speaking()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001  l'annonce est un confort : la conversation continue sans
            d.rec("tts_error", error=f"{type(e).__name__}: {e}"[:200], kind="search_announce")
        if sent:
            r.waiting(start, r.durs[idx])
            return
        r.spoken.remove(text)  # rien n'a été entendu : ni dans l'historique ni dans la troncature
        r.texts[idx] = ""
        if d.search_fillers is not None:
            f = d.search_fillers.pick()
            r.texts[idx], r.durs[idx] = f.text, f.dur
            r.spoken.append(f.text)
            r.waiting(self._since_detect(), f.dur)
            await self._emit(f)

    async def wait_more(self) -> None:
        """La recherche traîne : une 2e phrase brève évite un long silence, sauf si la réponse est déjà en route."""
        await asyncio.sleep(self.cfg.search_wait_after_s)
        await asyncio.gather(*self.announces, return_exceptions=True)  # après l'annonce, pour respecter l'ordre de lecture
        if len(self.resp.texts) == self.resp.off and self.resp.first_content_s is None:
            await self.play_filler(self.deps.wait_fillers.pick(), "search_wait")

    async def on_search(self, query: str, announce: str = "") -> None:
        """Le LLM cherche sur internet : l'agent dit ce qu'il cherche (ou une accroche générique) pour couvrir l'attente, sauf
        s'il a déjà commencé sa réponse (les rangs des morceaux ne doivent pas se décaler)."""
        r, d = self.resp, self.deps
        r.searched = True
        d.rec("search_notice", chars=len(query), announced=bool(announce))
        await d.send_json({"type": "searching"})
        if len(r.texts) > r.off:
            return
        if announce:
            self.announces.append(self.tasks.spawn_aux(self.announce_search(announce)))  # en tâche : ne retarde pas la recherche
        elif d.search_fillers is not None:
            await self.play_filler(d.search_fillers.pick(), "search")
        if d.wait_fillers is not None and self.cfg.search_wait_after_s > 0:
            self.tasks.spawn_aux(self.wait_more())
