"""Append an explicit human annotation for E5/E6 to an existing finished session."""

import argparse
import json
import time
from pathlib import Path

from voiceagent.evaluation.metrics import read_events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--turn", type=int, required=True)
    parser.add_argument("--experiment", choices=["E5-AEC", "E5-no-AEC", "E6"], required=True)
    parser.add_argument("--expected", choices=["true", "false"], required=True)
    parser.add_argument(
        "--outcome", choices=["handled", "missed", "false", "rejected"], required=True
    )
    args = parser.parse_args()
    path = args.session / "events.jsonl"
    events = read_events(path)
    if not events or events[-1]["event"] not in {"session_end", "interruption_result"}:
        parser.error("Fermer la session avant annotation")
    if args.turn not in {event["turn"] for event in events}:
        parser.error("Tour absent de la session")
    if any(
        event["event"] == "interruption_result" and event["turn"] == args.turn
        for event in events
    ):
        parser.error("Ce tour est déjà annoté ; corriger explicitement le JSONL si nécessaire")
    expected = args.expected == "true"
    valid = {"handled", "missed"} if expected else {"false", "rejected"}
    if args.outcome not in valid:
        parser.error("Combinaison expected/outcome incohérente")
    base = events[0]
    record = {
        "t_ns": time.perf_counter_ns(),
        "session": base["session"],
        "turn": args.turn,
        "event": "interruption_result",
        "mode": base.get("mode", "live"),
        "route": "local",
        "data": {
            "expected": expected,
            "outcome": args.outcome,
            "experiment": args.experiment,
            "annotation": "human",
        },
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
