from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


REQUIRED_COLUMNS = [
    "schema_id",
    "strategy",
    "first_try_valid",
    "final_valid",
    "attempts",
    "latency_ms",
    "cost",
    "error_type",
    "provider",
    "model",
]


def validate_result_columns(df: pd.DataFrame) -> None:
    """
    Vérifie que toutes les colonnes nécessaires sont présentes.
    """
    missing_columns = [
        column
        for column in REQUIRED_COLUMNS
        if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            "Missing required columns: "
            + ", ".join(missing_columns)
        )


def results_to_dataframe(
    results: List[Dict[str, Any]]
) -> pd.DataFrame:
    """
    Convertit une liste de résultats expérimentaux en DataFrame.
    """
    df = pd.DataFrame(results)
    validate_result_columns(df)
    return df


def load_results_jsonl(
    path: str | Path
) -> pd.DataFrame:
    """
    Charge les résultats depuis un fichier JSONL.
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"Results file not found: {path}"
        )

    df = pd.read_json(
        path,
        lines=True,
    )

    validate_result_columns(df)
    return df


def load_results_csv(
    path: str | Path
) -> pd.DataFrame:
    """
    Charge les résultats depuis un fichier CSV.
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"Results file not found: {path}"
        )

    df = pd.read_csv(path)

    validate_result_columns(df)
    return df


def load_results(
    path: str | Path
) -> pd.DataFrame:
    """
    Charge automatiquement un fichier CSV ou JSONL.
    """
    path = Path(path)

    if path.suffix.lower() == ".jsonl":
        return load_results_jsonl(path)

    if path.suffix.lower() == ".csv":
        return load_results_csv(path)

    raise ValueError(
        "Unsupported file format. Use .jsonl or .csv"
    )


def compute_global_metrics(
    df: pd.DataFrame
) -> Dict[str, float]:
    """
    Calcule les métriques globales du projet.
    """
    total = len(df)

    if total == 0:
        return {
            "total_generations": 0,
            "initial_compliance_rate": 0.0,
            "final_compliance_rate": 0.0,
            "final_failure_rate": 0.0,
            "avg_attempts": 0.0,
            "avg_latency_ms": 0.0,
            "total_cost": 0.0,
            "avg_cost": 0.0,
        }

    return {
        "total_generations": int(total),
        "initial_compliance_rate": float(
            df["first_try_valid"].mean()
        ),
        "final_compliance_rate": float(
            df["final_valid"].mean()
        ),
        "final_failure_rate": float(
            (~df["final_valid"].astype(bool)).mean()
        ),
        "avg_attempts": float(
            df["attempts"].mean()
        ),
        "avg_latency_ms": float(
            df["latency_ms"].mean()
        ),
        "total_cost": float(
            df["cost"].sum()
        ),
        "avg_cost": float(
            df["cost"].mean()
        ),
    }


def compute_metrics_by_schema(
    df: pd.DataFrame
) -> pd.DataFrame:
    """
    Calcule les métriques regroupées par schéma.
    """
    if df.empty:
        return pd.DataFrame()

    metrics = (
        df.groupby("schema_id")
        .agg(
            generations=("schema_id", "size"),
            initial_compliance_rate=(
                "first_try_valid",
                "mean",
            ),
            final_compliance_rate=(
                "final_valid",
                "mean",
            ),
            avg_attempts=("attempts", "mean"),
            avg_latency_ms=("latency_ms", "mean"),
            total_cost=("cost", "sum"),
            avg_cost=("cost", "mean"),
        )
        .reset_index()
    )

    metrics["final_failure_rate"] = (
        1.0
        - metrics["final_compliance_rate"]
    )

    return metrics


def compute_metrics_by_strategy(
    df: pd.DataFrame
) -> pd.DataFrame:
    """
    Calcule les métriques regroupées par stratégie.
    """
    if df.empty:
        return pd.DataFrame()

    metrics = (
        df.groupby("strategy")
        .agg(
            generations=("strategy", "size"),
            initial_compliance_rate=(
                "first_try_valid",
                "mean",
            ),
            final_compliance_rate=(
                "final_valid",
                "mean",
            ),
            avg_attempts=("attempts", "mean"),
            avg_latency_ms=("latency_ms", "mean"),
            total_cost=("cost", "sum"),
            avg_cost=("cost", "mean"),
        )
        .reset_index()
    )

    metrics["final_failure_rate"] = (
        1.0
        - metrics["final_compliance_rate"]
    )

    return metrics


def compute_metrics_by_schema_and_strategy(
    df: pd.DataFrame
) -> pd.DataFrame:
    """
    Calcule les métriques pour chaque couple schéma / stratégie.
    """
    if df.empty:
        return pd.DataFrame()

    metrics = (
        df.groupby(
            ["schema_id", "strategy"]
        )
        .agg(
            generations=("schema_id", "size"),
            initial_compliance_rate=(
                "first_try_valid",
                "mean",
            ),
            final_compliance_rate=(
                "final_valid",
                "mean",
            ),
            avg_attempts=("attempts", "mean"),
            avg_latency_ms=("latency_ms", "mean"),
            total_cost=("cost", "sum"),
            avg_cost=("cost", "mean"),
        )
        .reset_index()
    )

    metrics["final_failure_rate"] = (
        1.0
        - metrics["final_compliance_rate"]
    )

    return metrics