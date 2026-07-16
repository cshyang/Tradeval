"""Deterministic evaluation artifacts: evaluation.json, report.md, comparison.md.

evaluation.json matches the frozen fixture shape (run_id, as_of, metrics,
fidelity — floats rounded to 4 decimal places). Reports always carry the
research-only disclaimer.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from retailtrader.domain import EvaluationReport, ExperimentManifest
from retailtrader.storage.events import to_jsonable

DISCLAIMER = """\
> **Research only — not financial advice.** Historical replay is descriptive,
> not predictive. The fixed demo universe introduces selection bias. Short
> paper-trading periods do not establish an edge. Nothing here is a
> recommendation to buy or sell any security."""

_METRIC_LABELS = {
    "total_return": "Total return",
    "cagr": "CAGR",
    "volatility": "Annualized volatility",
    "sharpe": "Sharpe ratio",
    "max_drawdown": "Maximum drawdown",
    "turnover": "Turnover",
    "trade_count": "Trade count",
    "avg_holding_days": "Average holding days",
    "cash_exposure": "Cash exposure",
    "max_concentration": "Maximum concentration",
    "spy_relative": "Return vs SPY",
    "equal_weight_relative": "Return vs equal weight",
}

_FIDELITY_LABELS = {
    "factor_coverage": "Factor coverage (missing data)",
    "constraint_interventions": "Constraint interventions",
    "ranking_churn": "Ranking churn",
    "selection_stability": "Selection stability",
    "rule_violations": "Rule violations",
}


def _rounded(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: round(value, 4) if isinstance(value, float) else value
        for key, value in values.items()
    }


def evaluation_payload(report: EvaluationReport) -> dict[str, Any]:
    """Fixture-shaped evaluation.json payload (schema/engine versions omitted)."""
    return {
        "run_id": report.run_id,
        "as_of": to_jsonable(report.as_of),
        "metrics": _rounded(report.metrics.model_dump()),
        "fidelity": _rounded(report.fidelity.model_dump()),
    }


def write_evaluation_json(report: EvaluationReport, path: Path) -> None:
    path.write_text(json.dumps(evaluation_payload(report), indent=2) + "\n", encoding="utf-8")


def _metric_rows(values: dict[str, Any], labels: dict[str, str]) -> list[str]:
    rounded = _rounded(values)
    return [f"| {labels[key]} | {rounded[key]} |" for key in labels]


def render_report_md(manifest: ExperimentManifest, report: EvaluationReport) -> str:
    lines = [
        f"# Evaluation — {manifest.philosophy_name} {manifest.philosophy_version}",
        "",
        DISCLAIMER,
        "",
        "## Experiment identity",
        "",
        "| Input | Value |",
        "| --- | --- |",
        f"| Experiment id | {manifest.id} |",
        f"| Run id | {manifest.run_id} |",
        f"| Philosophy hash | {manifest.philosophy_hash} |",
        f"| Universe hash | {manifest.universe_hash} |",
        f"| Engine version | {manifest.engine_version} |",
        f"| Cadence | {manifest.cadence} |",
        f"| Window | {manifest.start.isoformat()} to {manifest.end.isoformat()} |",
        "",
        "## Performance",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        *_metric_rows(report.metrics.model_dump(), _METRIC_LABELS),
        "",
        "## Philosophy fidelity",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        *_metric_rows(report.fidelity.model_dump(), _FIDELITY_LABELS),
        "",
    ]
    return "\n".join(lines)


def write_report_md(manifest: ExperimentManifest, report: EvaluationReport, path: Path) -> None:
    path.write_text(render_report_md(manifest, report), encoding="utf-8")


def render_comparison_md(
    runs: Sequence[tuple[ExperimentManifest, EvaluationReport]],
) -> str:
    if not runs:
        raise ValueError("comparison requires at least one run")
    header = ["Metric", *(f"{m.philosophy_name} {m.philosophy_version}" for m, _ in runs)]
    lines = [
        "# Philosophy comparison",
        "",
        DISCLAIMER,
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    metric_dumps = [_rounded(report.metrics.model_dump()) for _, report in runs]
    fidelity_dumps = [_rounded(report.fidelity.model_dump()) for _, report in runs]
    for key, label in _METRIC_LABELS.items():
        cells = [str(dump[key]) for dump in metric_dumps]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    for key, label in _FIDELITY_LABELS.items():
        cells = [str(dump[key]) for dump in fidelity_dumps]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def write_comparison_md(
    runs: Sequence[tuple[ExperimentManifest, EvaluationReport]], path: Path
) -> None:
    path.write_text(render_comparison_md(runs), encoding="utf-8")
