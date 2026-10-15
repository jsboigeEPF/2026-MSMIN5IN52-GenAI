import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]

SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(
        0,
        str(SRC_DIR),
    )


from json_stress.report import (  # noqa: E402
    generate_full_report,
    generate_markdown_report,
    plot_compliance_by_schema,
    plot_strategy_comparison,
    save_tables,
)


def build_test_dataframe():
    return pd.DataFrame(
        [
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
    )


def test_save_tables(tmp_path):
    df = build_test_dataframe()

    paths = save_tables(
        df,
        tmp_path,
    )

    assert paths["by_schema"].exists()

    assert paths["by_strategy"].exists()

    assert paths["by_schema_strategy"].exists()


def test_generate_markdown_report(
    tmp_path
):
    df = build_test_dataframe()

    report_path = (
        generate_markdown_report(
            df,
            tmp_path,
        )
    )

    assert report_path.exists()

    content = report_path.read_text(
        encoding="utf-8"
    )

    assert (
        "JSON Stress — Evaluation Report"
        in content
    )

    assert (
        "Initial compliance rate"
        in content
    )


def test_compliance_chart(tmp_path):
    df = build_test_dataframe()

    chart_path = (
        plot_compliance_by_schema(
            df,
            tmp_path,
        )
    )

    assert chart_path.exists()

    assert (
        chart_path.suffix
        == ".png"
    )


def test_strategy_chart(tmp_path):
    df = build_test_dataframe()

    chart_path = (
        plot_strategy_comparison(
            df,
            tmp_path,
        )
    )

    assert chart_path.exists()

    assert (
        chart_path.suffix
        == ".png"
    )


def test_generate_full_report(
    tmp_path
):
    df = build_test_dataframe()

    artifacts = generate_full_report(
        df,
        tmp_path,
    )

    assert (
        artifacts["markdown_report"].exists()
    )

    assert (
        artifacts["compliance_chart"].exists()
    )

    assert (
        artifacts["strategy_chart"].exists()
    )

    assert (
        artifacts["latency_chart"].exists()
    )

    assert (
        artifacts["cost_chart"].exists()
    )