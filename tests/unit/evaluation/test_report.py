"""Report artifacts: evaluation.json matches the frozen fixture shape and the
Markdown reports carry identity, benchmarks, and the research-only disclaimer."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from retailtrader.domain import (
    EvaluationMetrics,
    EvaluationReport,
    FidelityMetrics,
)
from retailtrader.evaluation.report import (
    evaluation_payload,
    render_comparison_md,
    render_report_md,
    write_evaluation_json,
)
from tests.helpers import make_experiment

FIXTURE = (
    Path(__file__).parents[3]
    / "tests/fixtures/demo-run/exp-trend-v1-2024/evaluation.json"
)


def make_report(run_id: str = "run-test") -> EvaluationReport:
    return EvaluationReport(
        run_id=run_id,
        as_of=datetime(2024, 1, 19, 20, tzinfo=UTC),
        metrics=EvaluationMetrics(
            total_return=0.123456789,
            cagr=0.1,
            volatility=0.15,
            sharpe=1.2,
            max_drawdown=-0.08,
            turnover=0.17,
            trade_count=42,
            avg_holding_days=21.5,
            cash_exposure=0.05,
            max_concentration=0.12,
            spy_relative=0.02,
            equal_weight_relative=0.03,
        ),
        fidelity=FidelityMetrics(
            factor_coverage=0.95,
            constraint_interventions=3,
            ranking_churn=0.2,
            selection_stability=0.75,
            rule_violations=0,
        ),
    )


def test_evaluation_json_shape_matches_the_frozen_fixture(tmp_path: Path) -> None:
    fixture = json.loads(FIXTURE.read_text())
    path = tmp_path / "evaluation.json"
    write_evaluation_json(make_report(), path)
    written = json.loads(path.read_text())
    assert set(written) == set(fixture)
    assert set(written["metrics"]) == set(fixture["metrics"])
    assert set(written["fidelity"]) == set(fixture["fidelity"])
    assert isinstance(written["as_of"], str)
    assert written["metrics"]["total_return"] == 0.1235  # rounded to 4 decimals
    assert written["metrics"]["trade_count"] == 42  # ints stay ints


def test_report_md_includes_identity_benchmarks_and_disclaimer() -> None:
    manifest = make_experiment()
    text = render_report_md(manifest, make_report())
    assert "Research only" in text
    assert "not financial advice" in text
    assert "descriptive" in text
    assert "selection bias" in text
    assert "do not establish an edge" in text
    assert manifest.philosophy_hash in text
    assert manifest.universe_hash in text
    assert manifest.engine_version in text
    assert "Return vs SPY" in text
    assert "Constraint interventions | 3" in text


def test_comparison_md_covers_all_runs_and_disclaimer() -> None:
    runs = [(make_experiment(), make_report()), (make_experiment("run-2"), make_report("run-2"))]
    text = render_comparison_md(runs)
    assert "Research only" in text
    assert text.count("stub v1") == 2
    assert "| Sharpe ratio | 1.2 | 1.2 |" in text
    assert "| Selection stability | 0.75 | 0.75 |" in text


def test_evaluation_payload_omits_schema_and_engine_versions_like_fixture() -> None:
    payload = evaluation_payload(make_report())
    assert "schema_version" not in payload
    assert "engine_version" not in payload
