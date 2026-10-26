"""python -m voiceagent.report runs/<session>"""

import argparse
import csv
import json
from pathlib import Path

from voiceagent.evaluation.metrics import STAGES, read_events, summarize, turn_rows


def plot(rows: list[dict], directory: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    stages = list(STAGES)[:6]
    groups = sorted({row["mode"] for row in rows})
    for mode in groups:
        group = [row for row in rows if row["mode"] == mode]
        complete = [row for row in group if all(stage in row for stage in stages)]
        if complete:
            fig, axis = plt.subplots(figsize=(11, 5))
            bottom = np.zeros(len(complete))
            for stage in stages:
                values = np.array([row[stage] for row in complete])
                axis.bar(range(len(complete)), values, bottom=bottom, label=stage)
                bottom += values
            axis.set(
                xlabel="Tour (ordre du fichier)",
                ylabel="Latence (ms)",
                title=f"Étapes de réponse - {mode} - n={len(complete)}",
            )
            axis.legend(fontsize=8)
            fig.tight_layout()
            fig.savefig(directory / f"stages-{mode}.png", dpi=160)
            plt.close(fig)
        values = [row["response_ms"] for row in group if "response_ms" in row]
        if values:
            fig, axis = plt.subplots(figsize=(8, 4.5))
            axis.hist(
                values, bins=min(20, max(3, len(values) // 2)), color="#2563eb", alpha=0.85
            )
            for percentile, color in ((50, "#0f766e"), (95, "#e11d48")):
                value = np.percentile(values, percentile)
                axis.axvline(value, color=color, label=f"p{percentile} = {value:.1f} ms")
            axis.set(
                xlabel="Dernière trame de parole → premier audio (ms)",
                ylabel="Tours",
                title=f"Latence de réponse - {mode} - n={len(values)}",
            )
            axis.legend()
            fig.tight_layout()
            fig.savefig(directory / f"latency-{mode}.png", dpi=160)
            plt.close(fig)


def build_report(directory: Path, charts: bool = True) -> dict:
    events = read_events(directory / "events.jsonl")
    rows = turn_rows(events)
    summary = summarize(rows, events)
    fields = sorted({key for row in rows for key in row})
    with (directory / "turns.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    summary_rows = []
    for group, metrics in summary["groups"].items():
        print(f"\n{group} : {metrics['completed']}/{metrics['turns']} tours complets")
        print(f"{'Étape':24} {'n':>4} {'p50 ms':>10} {'p95 ms':>10}  IC95 du p50")
        for metric in STAGES:
            values = metrics[metric]
            summary_rows.append({"group": group, "metric": metric, **values})
            if values["n"]:
                print(
                    f"{metric:24} {values['n']:4d} {values['p50']:10.1f} {values['p95']:10.1f}"
                    f"  [{values['p50_ci_low']:.1f}, {values['p50_ci_high']:.1f}]"
                )
                if not values["p95_reliable"]:
                    print(
                        f"  n < 20 : min={values['min']:.1f}, max={values['max']:.1f}"
                        "; p95 fragile"
                    )
    fields = sorted({key for row in summary_rows for key in row})
    with (directory / "summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary_rows)
    (directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    if charts:
        plot(rows, directory)
    print(summary["warning"])
    if summary["wer"]:
        print("WER :", json.dumps(summary["wer"], ensure_ascii=False))
    if summary["interruptions"]:
        print("Interruptions :", json.dumps(summary["interruptions"], ensure_ascii=False))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    arguments = parser.parse_args()
    build_report(arguments.session)
