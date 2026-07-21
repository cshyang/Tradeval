"""Deterministic evaluation artifacts: evaluation.json, report.md, comparison.md.

evaluation.json matches the frozen fixture shape (run_id, as_of, metrics,
fidelity — floats rounded to 4 decimal places). Reports always carry the
research-only disclaimer.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
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
    "synthetic_mega_cap_proxy_relative": "Return vs synthetic mega-cap proxy",
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
    """Stable evaluation.json payload including its contract versions."""
    return {
        "run_id": report.run_id,
        "as_of": to_jsonable(report.as_of),
        "schema_version": report.schema_version,
        "engine_version": report.engine_version,
        "metrics": _rounded(report.metrics.model_dump()),
        "fidelity": _rounded(report.fidelity.model_dump()),
    }


def write_evaluation_json(report: EvaluationReport, path: Path) -> None:
    path.write_text(json.dumps(evaluation_payload(report), indent=2) + "\n", encoding="utf-8")


def _metric_rows(values: dict[str, Any], labels: dict[str, str]) -> list[str]:
    rounded = _rounded(values)
    return [f"| {labels[key]} | {rounded[key]} |" for key in labels]


def _provenance_value(value: Any) -> str:
    serialized = to_jsonable(value)
    if isinstance(serialized, (dict, list)):
        return json.dumps(serialized, sort_keys=True)
    return str(serialized)


def render_report_md(
    manifest: ExperimentManifest,
    report: EvaluationReport,
    data_provenance: Mapping[str, Any] | None = None,
) -> str:
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
        f"| Data source | {manifest.data_source} |",
        f"| Benchmark source | {manifest.benchmark_source} |",
        f"| Initial cash | {manifest.initial_cash} |",
        f"| Slippage (bps) | {manifest.slippage_bps} |",
        f"| Cadence | {manifest.cadence} |",
        f"| Window | {manifest.start.isoformat()} to {manifest.end.isoformat()} |",
        "",
    ]
    if data_provenance is not None:
        labels = {
            "kind": "Data kind",
            "validity": "Validity",
            "label": "Display label",
            "transport": "Transport",
            "provider": "Provider",
            "provider_versions": "Provider versions",
            "adjustment": "Adjustment",
            "retrieved_at": "Retrieved at",
            "query_hash": "Query hash",
            "normalized_hash": "Normalized data hash",
            "benchmark_kind": "Benchmark kind",
            "reference_method_version": "Reference method",
            "execution_model_version": "Execution model",
        }
        lines.extend(["## Data provenance", "", "| Input | Value |", "| --- | --- |"])
        for key, label in labels.items():
            if key in data_provenance:
                lines.append(f"| {label} | {_provenance_value(data_provenance[key])} |")
        warnings = data_provenance.get("warnings", [])
        if warnings:
            lines.extend(["", "### Provenance warnings", ""])
            lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    metric_labels = dict(_METRIC_LABELS)
    if data_provenance is not None and data_provenance.get("kind") == "real_market":
        metric_labels["synthetic_mega_cap_proxy_relative"] = "Return vs SPY"
    lines.extend([
        "## Performance",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        *_metric_rows(report.metrics.model_dump(), metric_labels),
        "",
        "## Philosophy fidelity",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        *_metric_rows(report.fidelity.model_dump(), _FIDELITY_LABELS),
        "",
    ])
    return "\n".join(lines)


def write_report_md(
    manifest: ExperimentManifest,
    report: EvaluationReport,
    path: Path,
    data_provenance: Mapping[str, Any] | None = None,
) -> None:
    path.write_text(
        render_report_md(manifest, report, data_provenance), encoding="utf-8"
    )


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
        "Data and benchmark sources:",
        *[
            f"- {manifest.data_source}; {manifest.benchmark_source}"
            for manifest, _ in runs
        ],
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    metric_dumps = [_rounded(report.metrics.model_dump()) for _, report in runs]
    fidelity_dumps = [_rounded(report.fidelity.model_dump()) for _, report in runs]
    metric_labels = dict(_METRIC_LABELS)
    if all(manifest.benchmark_source.startswith("spy-") for manifest, _ in runs):
        metric_labels["synthetic_mega_cap_proxy_relative"] = "Return vs SPY"
    for key, label in metric_labels.items():
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
