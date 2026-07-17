"""Engine-owned provenance export tests."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from retailtrader.cli import app


def _demo(workspace: Path) -> list[Path]:
    result = CliRunner().invoke(
        app,
        [
            "demo",
            "--workspace",
            str(workspace),
            "--start",
            "2024-01-05",
            "--end",
            "2024-01-19",
        ],
    )
    assert result.exit_code == 0, result.output
    return sorted(path for path in workspace.iterdir() if path.is_dir())


def test_export_copies_and_aggregates_engine_provenance(tmp_path: Path) -> None:
    workspace = tmp_path / "runs"
    run_dirs = _demo(workspace)
    real_run = run_dirs[0]
    provenance_path = real_run / "data-provenance.json"
    provenance = json.loads(provenance_path.read_text())
    provenance.update(
        {
            "kind": "real_market",
            "validity": "hindsight_current_universe",
            "label": "HINDSIGHT · ADJUSTED MARKET DATA",
            "transport": "openbb",
            "provider": "yfinance",
            "provider_versions": [["openbb", "4.7.2"]],
            "adjustment": "splits_and_dividends",
            "retrieved_at": "2025-01-01T12:00:00+00:00",
            "query_hash": "a" * 64,
            "normalized_hash": "b" * 64,
            "source_refs": ["large-internal-source-list-must-not-enter-view-model"],
        }
    )
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n")
    out = tmp_path / "public" / "runs"

    result = CliRunner().invoke(
        app,
        ["export", "--workspace", str(workspace), "--out", str(out)],
    )

    assert result.exit_code == 0, result.output
    data = json.loads((out / "data.json").read_text())
    by_id = {experiment["id"]: experiment for experiment in data["experiments"]}
    real = by_id[json.loads((real_run / "manifest.json").read_text())["id"]]
    assert real["data_provenance"]["label"] == "HINDSIGHT · ADJUSTED MARKET DATA"
    assert real["data_provenance"]["provider"] == "yfinance"
    assert real["data_provenance"]["normalized_hash"] == "b" * 64
    assert "source_refs" not in real["data_provenance"]
    synthetic = [
        experiment
        for experiment in data["experiments"]
        if experiment["id"] != real["id"]
    ]
    assert {item["data_provenance"]["label"] for item in synthetic} == {
        "SYNTHETIC DEMO DATA"
    }
    assert (out / real_run.name / "data-provenance.json").exists()


def test_export_rejects_missing_provenance_without_defaulting(tmp_path: Path) -> None:
    workspace = tmp_path / "runs"
    run_dirs = _demo(workspace)
    (run_dirs[0] / "data-provenance.json").unlink()
    out = tmp_path / "public" / "runs"

    result = CliRunner().invoke(
        app,
        ["export", "--workspace", str(workspace), "--out", str(out)],
    )

    assert result.exit_code == 1
    assert "missing ['data-provenance.json']" in result.output
    assert not (out / "data.json").exists()
