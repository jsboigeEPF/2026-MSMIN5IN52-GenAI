"""
Command-line interface for the JSON Stress project.

Current scope:
- evaluate an existing CSV/JSONL experiment file
- generate report artifacts

This module intentionally does not depend on generator.py,
validator.py or retry.py yet.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .evaluation import (
    compute_global_metrics,
    compute_metrics_by_schema,
    compute_metrics_by_strategy,
    load_results,
)
from .report import generate_full_report


def build_parser() -> argparse.ArgumentParser:
    """
    Construit le parser principal de l'interface en ligne de commande.
    """
    parser = argparse.ArgumentParser(
        prog="json-stress",
        description=(
            "Evaluation and reporting tools "
            "for the JSON Stress project."
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    # Commande : evaluate
    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="Evaluate an existing experiment result file.",
    )

    evaluate_parser.add_argument(
        "input",
        help="Path to a .jsonl or .csv result file.",
    )

    # Commande : report
    report_parser = subparsers.add_parser(
        "report",
        help=(
            "Generate CSV tables, PNG charts "
            "and a Markdown report."
        ),
    )

    report_parser.add_argument(
        "input",
        help="Path to a .jsonl or .csv result file.",
    )

    report_parser.add_argument(
        "--output-dir",
        default="results/report",
        help=(
            "Directory where the report artifacts "
            "will be written."
        ),
    )

    return parser


def print_global_metrics(metrics: dict) -> None:
    """
    Affiche les métriques globales de manière lisible.
    """
    print("\n=== Global metrics ===")

    print(
        f"Total generations: "
        f"{metrics['total_generations']}"
    )

    print(
        f"Initial compliance rate: "
        f"{metrics['initial_compliance_rate']:.2%}"
    )

    print(
        f"Final compliance rate: "
        f"{metrics['final_compliance_rate']:.2%}"
    )

    print(
        f"Final failure rate: "
        f"{metrics['final_failure_rate']:.2%}"
    )

    print(
        f"Average attempts: "
        f"{metrics['avg_attempts']:.2f}"
    )

    print(
        f"Average latency: "
        f"{metrics['avg_latency_ms']:.2f} ms"
    )

    print(
        f"Total cost: "
        f"{metrics['total_cost']:.6f}"
    )

    print(
        f"Average cost: "
        f"{metrics['avg_cost']:.6f}"
    )


def run_evaluate(input_path: str) -> None:
    """
    Charge un fichier de résultats et affiche les métriques.
    """
    df = load_results(input_path)

    global_metrics = compute_global_metrics(df)
    by_schema = compute_metrics_by_schema(df)
    by_strategy = compute_metrics_by_strategy(df)

    print_global_metrics(global_metrics)

    print("\n=== Metrics by schema ===")

    if by_schema.empty:
        print("No data.")
    else:
        print(
            by_schema.to_string(
                index=False
            )
        )

    print("\n=== Metrics by strategy ===")

    if by_strategy.empty:
        print("No data.")
    else:
        print(
            by_strategy.to_string(
                index=False
            )
        )


def run_report(
    input_path: str,
    output_dir: str,
) -> None:
    """
    Génère tous les artefacts du rapport.
    """
    df = load_results(input_path)

    artifacts = generate_full_report(
        df,
        output_dir,
    )

    resolved_output = Path(
        output_dir
    ).resolve()

    print("\n=== Report generated ===")

    print(
        f"Output directory: "
        f"{resolved_output}"
    )

    print(
        f"Markdown report: "
        f"{artifacts['markdown_report']}"
    )

    print(
        f"Compliance chart: "
        f"{artifacts['compliance_chart']}"
    )

    print(
        f"Strategy chart: "
        f"{artifacts['strategy_chart']}"
    )

    print(
        f"Latency chart: "
        f"{artifacts['latency_chart']}"
    )

    print(
        f"Cost chart: "
        f"{artifacts['cost_chart']}"
    )


def main() -> None:
    """
    Point d'entrée principal de la CLI.
    """
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "evaluate":
        run_evaluate(
            args.input
        )

    elif args.command == "report":
        run_report(
            args.input,
            args.output_dir,
        )


if __name__ == "__main__":
    main()