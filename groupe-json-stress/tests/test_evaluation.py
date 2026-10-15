import sys
from pathlib import Path

import pandas as pd
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


from json_stress.evaluation import (
    compute_global_metrics,
    compute_metrics_by_schema,
    compute_metrics_by_strategy,
    results_to_dataframe,
)


@pytest.fixture
def sample_results():
    return [
        {
            "schema_id": "S1",
            "strategy": "baseline",
            "first_try_valid": True,
            "final_valid": True,
            "attempts": 1,
            "latency_ms": 100.0,
            "cost": 0.001,
            "error_type": None,
            "provider": "mock",
            "model": "mock-v1",
        },
        {
            "schema_id": "S1",
            "strategy": "retry",
            "first_try_valid": False,
            "final_valid": True,
            "attempts": 2,
            "latency_ms": 200.0,
            "cost": 0.002,
            "error_type": None,
            "provider": "mock",
            "model": "mock-v1",
        },
        {
            "schema_id": "S2",
            "strategy": "baseline",
            "first_try_valid": False,
            "final_valid": False,
            "attempts": 1,
            "latency_ms": 150.0,
            "cost": 0.0015,
            "error_type": "schema_validation",
            "provider": "mock",
            "model": "mock-v1",
        },
        {
            "schema_id": "S2",
            "strategy": "retry",
            "first_try_valid": False,
            "final_valid": True,
            "attempts": 3,
            "latency_ms": 300.0,
            "cost": 0.003,
            "error_type": None,
            "provider": "mock",
            "model": "mock-v1",
        },
    ]


def test_results_to_dataframe(sample_results):
    df = results_to_dataframe(sample_results)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 4


def test_global_metrics(sample_results):
    df = results_to_dataframe(sample_results)

    metrics = compute_global_metrics(df)

    assert metrics["total_generations"] == 4
    assert metrics["initial_compliance_rate"] == pytest.approx(
        0.25
    )
    assert metrics["final_compliance_rate"] == pytest.approx(
        0.75
    )
    assert metrics["final_failure_rate"] == pytest.approx(
        0.25
    )
    assert metrics["avg_attempts"] == pytest.approx(
        1.75
    )
    assert metrics["avg_latency_ms"] == pytest.approx(
        187.5
    )
    assert metrics["total_cost"] == pytest.approx(
        0.0075
    )


def test_metrics_by_schema(sample_results):
    df = results_to_dataframe(sample_results)

    metrics = compute_metrics_by_schema(df)

    assert set(metrics["schema_id"]) == {
        "S1",
        "S2",
    }

    s1 = metrics[
        metrics["schema_id"] == "S1"
    ].iloc[0]

    assert s1["final_compliance_rate"] == pytest.approx(
        1.0
    )


def test_metrics_by_strategy(sample_results):
    df = results_to_dataframe(sample_results)

    metrics = compute_metrics_by_strategy(df)

    retry = metrics[
        metrics["strategy"] == "retry"
    ].iloc[0]

    assert retry["final_compliance_rate"] == pytest.approx(
        1.0
    )

    baseline = metrics[
        metrics["strategy"] == "baseline"
    ].iloc[0]

    assert baseline["final_compliance_rate"] == pytest.approx(
        0.5
    )


def test_missing_column_raises_error(
    sample_results
):
    broken_results = [
        {
            key: value
            for key, value
            in sample_results[0].items()
            if key != "schema_id"
        }
    ]

    with pytest.raises(ValueError):
        results_to_dataframe(
            broken_results
        )


def test_empty_dataframe():
    columns = [
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

    df = pd.DataFrame(
        columns=columns
    )

    metrics = compute_global_metrics(df)

    assert metrics["total_generations"] == 0
    assert (
        metrics["initial_compliance_rate"]
        == 0.0
    )
    assert (
        metrics["final_compliance_rate"]
        == 0.0
    )