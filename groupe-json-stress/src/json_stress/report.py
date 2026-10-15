"""
Report generation for JSON Stress experiments.

This module consumes evaluation results and generates:
- CSV summary tables
- PNG charts
- a Markdown report

It remains independent from the generator, validator and retry modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from .evaluation import (
    compute_global_metrics,
    compute_metrics_by_schema,
    compute_metrics_by_strategy,
    compute_metrics_by_schema_and_strategy,
)


def save_tables(
    df: pd.DataFrame,
    output_dir: str | Path,
) -> Dict[str, Path]:
    """
    Sauvegarde les tableaux de métriques au format CSV.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    by_schema = compute_metrics_by_schema(df)
    by_strategy = compute_metrics_by_strategy(df)
    by_schema_strategy = (
        compute_metrics_by_schema_and_strategy(df)
    )

    paths = {
        "by_schema":
            output_dir / "metrics_by_schema.csv",

        "by_strategy":
            output_dir / "metrics_by_strategy.csv",

        "by_schema_strategy":
            output_dir / "metrics_by_schema_strategy.csv",
    }

    by_schema.to_csv(
        paths["by_schema"],
        index=False,
    )

    by_strategy.to_csv(
        paths["by_strategy"],
        index=False,
    )

    by_schema_strategy.to_csv(
        paths["by_schema_strategy"],
        index=False,
    )

    return paths


def plot_compliance_by_schema(
    df: pd.DataFrame,
    output_dir: str | Path,
) -> Path:
    """
    Génère un graphique comparant la conformité initiale
    et finale pour chaque JSON Schema.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    metrics = compute_metrics_by_schema(df)

    output_path = (
        output_dir / "compliance_by_schema.png"
    )

    if metrics.empty:
        return output_path

    x = list(range(len(metrics)))

    width = 0.35

    plt.figure(
        figsize=(9, 5)
    )

    plt.bar(
        [
            value - width / 2
            for value in x
        ],
        metrics["initial_compliance_rate"],
        width=width,
        label="Initial compliance",
    )

    plt.bar(
        [
            value + width / 2
            for value in x
        ],
        metrics["final_compliance_rate"],
        width=width,
        label="Final compliance",
    )

    plt.xticks(
        x,
        metrics["schema_id"],
    )

    plt.ylim(
        0,
        1,
    )

    plt.xlabel(
        "JSON Schema"
    )

    plt.ylabel(
        "Compliance rate"
    )

    plt.title(
        "Initial vs final compliance by schema"
    )

    plt.legend()

    plt.tight_layout()

    plt.savefig(
        output_path
    )

    plt.close()

    return output_path


def plot_strategy_comparison(
    df: pd.DataFrame,
    output_dir: str | Path,
) -> Path:
    """
    Génère un graphique comparant la conformité finale
    des différentes stratégies.
    """
    output_dir = Path(output_dir)

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    metrics = compute_metrics_by_strategy(df)

    output_path = (
        output_dir / "strategy_comparison.png"
    )

    if metrics.empty:
        return output_path

    plt.figure(
        figsize=(8, 5)
    )

    plt.bar(
        metrics["strategy"],
        metrics["final_compliance_rate"],
    )

    plt.ylim(
        0,
        1,
    )

    plt.xlabel(
        "Strategy"
    )

    plt.ylabel(
        "Final compliance rate"
    )

    plt.title(
        "Final compliance by strategy"
    )

    plt.tight_layout()

    plt.savefig(
        output_path
    )

    plt.close()

    return output_path


def plot_latency_by_schema(
    df: pd.DataFrame,
    output_dir: str | Path,
) -> Path:
    """
    Génère un graphique de latence moyenne par schéma.
    """
    output_dir = Path(output_dir)

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    metrics = compute_metrics_by_schema(df)

    output_path = (
        output_dir / "latency_by_schema.png"
    )

    if metrics.empty:
        return output_path

    plt.figure(
        figsize=(8, 5)
    )

    plt.bar(
        metrics["schema_id"],
        metrics["avg_latency_ms"],
    )

    plt.xlabel(
        "JSON Schema"
    )

    plt.ylabel(
        "Average latency (ms)"
    )

    plt.title(
        "Average latency by schema"
    )

    plt.tight_layout()

    plt.savefig(
        output_path
    )

    plt.close()

    return output_path


def plot_cost_by_strategy(
    df: pd.DataFrame,
    output_dir: str | Path,
) -> Path:
    """
    Génère un graphique du coût total par stratégie.
    """
    output_dir = Path(output_dir)

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    metrics = compute_metrics_by_strategy(df)

    output_path = (
        output_dir / "cost_by_strategy.png"
    )

    if metrics.empty:
        return output_path

    plt.figure(
        figsize=(8, 5)
    )

    plt.bar(
        metrics["strategy"],
        metrics["total_cost"],
    )

    plt.xlabel(
        "Strategy"
    )

    plt.ylabel(
        "Total cost"
    )

    plt.title(
        "Total cost by strategy"
    )

    plt.tight_layout()

    plt.savefig(
        output_path
    )

    plt.close()

    return output_path


def generate_markdown_report(
    df: pd.DataFrame,
    output_dir: str | Path,
) -> Path:
    """
    Génère un rapport Markdown basé uniquement
    sur les résultats mesurés.
    """
    output_dir = Path(output_dir)

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    global_metrics = compute_global_metrics(df)

    by_schema = compute_metrics_by_schema(df)

    by_strategy = compute_metrics_by_strategy(df)

    report_path = (
        output_dir / "report.md"
    )

    content = [
        "# JSON Stress — Evaluation Report",
        "",
        "## Global metrics",
        "",
        (
            "- Total generations: "
            f"{global_metrics['total_generations']}"
        ),
        (
            "- Initial compliance rate: "
            f"{global_metrics['initial_compliance_rate']:.2%}"
        ),
        (
            "- Final compliance rate: "
            f"{global_metrics['final_compliance_rate']:.2%}"
        ),
        (
            "- Final failure rate: "
            f"{global_metrics['final_failure_rate']:.2%}"
        ),
        (
            "- Average attempts: "
            f"{global_metrics['avg_attempts']:.2f}"
        ),
        (
            "- Average latency: "
            f"{global_metrics['avg_latency_ms']:.2f} ms"
        ),
        (
            "- Total cost: "
            f"{global_metrics['total_cost']:.6f}"
        ),
        (
            "- Average cost: "
            f"{global_metrics['avg_cost']:.6f}"
        ),
        "",
        "## Metrics by schema",
        "",
        (
            by_schema.to_markdown(
                index=False
            )
            if not by_schema.empty
            else "No data available."
        ),
        "",
        "## Metrics by strategy",
        "",
        (
            by_strategy.to_markdown(
                index=False
            )
            if not by_strategy.empty
            else "No data available."
        ),
        "",
        "## Generated charts",
        "",
        "- `compliance_by_schema.png`",
        "- `strategy_comparison.png`",
        "- `latency_by_schema.png`",
        "- `cost_by_strategy.png`",
        "",
        "## Notes",
        "",
        (
            "All values in this report are computed "
            "from the supplied experiment results."
        ),
        (
            "No experimental result is inferred "
            "or fabricated."
        ),
        "",
    ]

    report_path.write_text(
        "\n".join(content),
        encoding="utf-8",
    )

    return report_path


def generate_full_report(
    df: pd.DataFrame,
    output_dir: str | Path,
) -> Dict[str, object]:
    """
    Génère tous les artefacts de rapport disponibles.
    """
    output_dir = Path(output_dir)

    tables = save_tables(
        df,
        output_dir,
    )

    compliance_chart = (
        plot_compliance_by_schema(
            df,
            output_dir,
        )
    )

    strategy_chart = (
        plot_strategy_comparison(
            df,
            output_dir,
        )
    )

    latency_chart = (
        plot_latency_by_schema(
            df,
            output_dir,
        )
    )

    cost_chart = (
        plot_cost_by_strategy(
            df,
            output_dir,
        )
    )

    markdown_report = (
        generate_markdown_report(
            df,
            output_dir,
        )
    )

    return {
        "tables": tables,
        "compliance_chart":
            compliance_chart,
        "strategy_chart":
            strategy_chart,
        "latency_chart":
            latency_chart,
        "cost_chart":
            cost_chart,
        "markdown_report":
            markdown_report,
    }