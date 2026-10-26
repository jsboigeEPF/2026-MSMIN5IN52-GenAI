"""Small terminal observer; presentation never enters audio callbacks."""

from voiceagent.evaluation.metrics import STAGES


class Console:
    def __init__(self):
        self._turns = {}

    def __call__(self, event: dict) -> None:
        turn = event["turn"]
        if not turn:
            return
        timing = self._turns.setdefault(turn, {})
        name = event["event"]
        timing.setdefault(name, event["t_ns"])
        if name == "stt_done":
            print(f"\nVous : {event['data']['text']}\nAgent : ", end="", flush=True)
        elif name == "playback_stopped":
            print(f"\n[Interruption : arrêt hôte {event['data']['stop_ms']:.1f} ms]")
        elif name == "error":
            print(f"\n[Erreur {event['data'].get('stage')} : {event['data'].get('exception')}]")
        elif name == "turn_done":
            values = []
            for metric, (start, end) in STAGES.items():
                if start in timing and end in timing:
                    duration = (timing[end] - timing[start]) / 1e6
                    values.append(f"{metric}={duration:.1f}")
            print("\n[" + " | ".join(values) + "]", flush=True)
        elif name in {"history_commit", "speech_discarded", "replay_missed"}:
            self._turns.pop(turn, None)
        # Defensive bound if input turns never reach a terminal event.
        while len(self._turns) > 16:
            self._turns.pop(next(iter(self._turns)))
