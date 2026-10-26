"""No imputed durations: missing, interrupted and failed turns remain visible."""

import json
from collections import defaultdict
from pathlib import Path

from voiceagent.evaluation.statistics import describe, wilson, word_errors

STAGES = {
    "endpoint_ms": ("speech_end_real", "vad_endpoint"),
    "stt_ms": ("vad_endpoint", "stt_done"),
    "llm_first_token_ms": ("stt_done", "llm_first_token"),
    "sentence_ms": ("llm_first_token", "llm_first_sentence"),
    "tts_ms": ("llm_first_sentence", "tts_first_chunk"),
    "playback_ms": ("tts_first_chunk", "playback_start"),
    "response_ms": ("speech_end_real", "playback_start"),
    "text_response_ms": ("text_input", "playback_start"),
    "stop_ms": ("barge_in_detected", "playback_stopped"),
}


def read_events(path: Path) -> list[dict]:
    events = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                for key in ("session", "turn", "t_ns", "event", "data"):
                    if key not in item:
                        raise ValueError(f"champ {key} absent")
                events.append(item)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Trace invalide : {path}:{line_number}: {exc}") from exc
    return events


def turn_rows(events: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for event in events:
        if event["turn"]:
            key = (
                event["session"],
                event.get("mode", "unknown"),
                event.get("route", "local"),
                event["turn"],
            )
            groups[key].append(event)
    rows = []
    for (session, mode, route, turn), items in groups.items():
        by_name = {}
        for item in sorted(items, key=lambda event: event["t_ns"]):
            by_name.setdefault(item["event"], item)
        row = {"session": session, "mode": mode, "route": route, "turn": turn}
        row["status"] = "complete" if "turn_done" in by_name else "incomplete"
        if "error" in by_name:
            row["status"] = "error"
        elif "turn_cancelled" in by_name:
            row["status"] = "interrupted"
        for metric, (start, end) in STAGES.items():
            if metric == "stop_ms":
                reason = by_name.get(start, {}).get("data", {}).get("reason")
                if reason in {"shutdown", "reset", "benchmark_cleanup"}:
                    continue
            if start in by_name and end in by_name:
                value = (by_name[end]["t_ns"] - by_name[start]["t_ns"]) / 1e6
                if value >= 0:
                    row[metric] = value
        reference = by_name.get("eval_reference", {}).get("data", {})
        if reference:
            row.update(
                {key: reference.get(key) for key in ("file", "noise", "snr_db", "source")}
            )
            text = by_name.get("stt_done", {}).get("data", {}).get("text", "")
            row.update(word_errors(reference["reference"], text))
        rows.append(row)
    return rows


def summarize(rows: list[dict], events: list[dict], seed: int = 42) -> dict:
    summary = {"groups": {}, "interruptions": {}, "wer": {}, "memory": {}}
    keys = sorted({(row["mode"], row["route"]) for row in rows})
    for mode, route in keys:
        group = [row for row in rows if row["mode"] == mode and row["route"] == route]
        metrics = {}
        for metric in STAGES:
            values = [row[metric] for row in group if metric in row]
            metrics[metric] = describe(values, seed)
        metrics["turns"] = len(group)
        metrics["completed"] = sum(row["status"] == "complete" for row in group)
        summary["groups"][f"{mode}/{route}"] = metrics

    wer_groups = defaultdict(list)
    for row in rows:
        if "words" in row:
            key = f"{row['mode']}/{row['route']}/{row['source']}/{row['noise']}/{row['snr_db']}"
            wer_groups[key].append(row)
    for key, group in wer_groups.items():
        words = sum(row["words"] for row in group)
        errors = sum(row["errors"] for row in group)
        summary["wer"][key] = {
            "n": len(group),
            "words": words,
            "errors": errors,
            "wer": errors / words if words else None,
        }

    annotated = defaultdict(list)
    for event in events:
        if event["event"] == "interruption_result":
            key = f"{event.get('mode', 'unknown')}/{event['data'].get('experiment', 'unknown')}"
            annotated[key].append(event["data"])
    for key, trials in annotated.items():
        real = [trial for trial in trials if trial["expected"]]
        false = [trial for trial in trials if not trial["expected"]]
        good = sum(trial["outcome"] == "handled" for trial in real)
        missed = sum(trial["outcome"] in {"missed", "false"} for trial in real)
        false_count = sum(trial["outcome"] == "false" for trial in false)
        summary["interruptions"][key] = {
            "handled": wilson(good, len(real)),
            "missed": wilson(missed, len(real)),
            "false": wilson(false_count, len(false)),
        }
    for key in ("device_used_mib", "torch_peak_mib"):
        values = [
            event["data"][key]
            for event in events
            if event["event"] == "memory_sample" and event["data"].get(key) is not None
        ]
        summary["memory"][key] = max(values) if values else None
    summary["cost_eur"] = 0.0
    summary["warning"] = (
        "p95 peu fiable pour n < 20. Les simulations ne valident pas le matériel."
    )
    return summary
