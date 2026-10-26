"""État d'une réponse en cours (un tour) : morceaux dits, durées d'audio, repères de latence."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from src.metrics.clock import now_ns
from src.orchestrator.truncation import heard_text


@dataclass
class ResponseState:
    texts: list[str] = field(default_factory=list)     # morceaux de la réponse (accroches comprises), dans l'ordre de lecture
    durs: list[float] = field(default_factory=list)    # secondes d'audio envoyées par morceau
    off: int = 0                                       # nombre de morceaux d'attente placés avant la réponse
    first_audio_at: float | None = None                # monotonic : premier audio envoyé (accroche comprise)
    first_audio_ns: int | None = None                  # monotonic_ns : même instant, pour les durées
    audible_ns: int | None = None                      # monotonic_ns estimé : premier échantillon audible (message `playback_started` du client)
    first_content_s: float | None = None               # s depuis `t_detect` : premier audio du contenu réel
    t_detect: int = 0                                  # monotonic_ns au début du tour (fin de parole détectée ou texte saisi)
    turn_complete: bool = False
    user_recorded: bool = False                        # la parole de l'utilisateur est transcrite
    current_audio: tuple[bytes, int] | None = None     # audio du tour (repris si interrompu avant transcription)
    spoken: list[str] = field(default_factory=list)    # accroches dites avant la réponse (pour l'historique)
    lead: list[float] = field(default_factory=list)    # [1er son de l'attente, fin estimée de l'attente], s depuis `t_detect`
    searched: bool = False
    ignored: bool = False                              # transcription vide/bruit : seule l'accroche a été dite, rien à mémoriser

    def reset(self) -> None:
        self.__dict__.update(ResponseState().__dict__)

    def add_chunk(self, text: str, dur: float = 0.0) -> int:
        self.texts.append(text)
        self.durs.append(dur)
        return len(self.texts) - 1

    def reserve(self, text: str, dur: float) -> int:
        """Réserve le rang d'une phrase d'attente (sans await : les rangs ne se décalent pas une fois la réponse partie)."""
        self.off += 1
        self.spoken.append(text)
        return self.add_chunk(text, dur)

    def waiting(self, start: float, dur: float) -> None:
        """Met à jour l'attente couverte par de l'audio de remplissage : 1er son et fin de lecture estimée."""
        self.lead[:] = [self.lead[0] if self.lead else start, max(self.lead[1] if self.lead else 0.0, start) + dur]

    def mark_first_audio(self) -> None:
        if self.first_audio_at is None:
            self.first_audio_at, self.first_audio_ns = time.monotonic(), now_ns()

    @property
    def audio_s(self) -> float:
        return sum(self.durs)

    def played_s(self, now: float, margin_s: float) -> float:
        """Durée jouée estimée côté serveur à l'instant `now` (monotonic)."""
        return max(0.0, now - self.first_audio_at - margin_s) if self.first_audio_at else 0.0

    def played_text(self, played_s: float) -> tuple[str, bool]:
        return heard_text(self.texts, self.durs, played_s, self.turn_complete)
