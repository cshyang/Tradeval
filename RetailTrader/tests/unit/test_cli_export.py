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
    out = tmp_path / "public" / "runs"

    result = CliRunner().invoke(
        app,
        ["export", "--workspace", str(workspace), "--out", str(out)],
    )

    assert result.exit_code == 0, result.output
    data = json.loads((out / "data.json").read_text())
    assert data["data_provenance"]["label"] == "SYNTHETIC DEMO DATA"
    assert {item["data_provenance"]["label"] for item in data["experiments"]} == {
        "SYNTHETIC DEMO DATA"
    }
    assert {item["data_provenance"]["provider"] for item in data["experiments"]} == {
        "synthetic"
    }
    assert all(
        "source_refs" not in item["data_provenance"]
        for item in data["experiments"]
    )
    assert all((out / run_dir.name / "data-provenance.json").exists() for run_dir in run_dirs)


def test_export_rejects_provenance_that_disagrees_with_manifest(tmp_path: Path) -> None:
    workspace = tmp_path / "runs"
    run_dirs = _demo(workspace)
    provenance_path = run_dirs[0] / "data-provenance.json"
    provenance = json.loads(provenance_path.read_text())
    provenance["kind"] = "real_market"
    provenance["label"] = "HINDSIGHT · ADJUSTED MARKET DATA"
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n")
    out = tmp_path / "public" / "runs"

    result = CliRunner().invoke(
        app,
        ["export", "--workspace", str(workspace), "--out", str(out)],
    )

    assert result.exit_code == 5
    assert "provenance" in result.output
    assert not out.exists()


def test_export_rejects_missing_provenance_without_defaulting(tmp_path: Path) -> None:
    workspace = tmp_path / "runs"
    run_dirs = _demo(workspace)
    (run_dirs[0] / "data-provenance.json").unlink()
    out = tmp_path / "public" / "runs"

    result = CliRunner().invoke(
        app,
        ["export", "--workspace", str(workspace), "--out", str(out)],
    )

    assert result.exit_code == 4
    assert "missing ['data-provenance.json']" in result.output
    assert not (out / "data.json").exists()
