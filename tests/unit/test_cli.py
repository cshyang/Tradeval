from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from retailtrader.cli import PHILOSOPHY_DIR, app

runner = CliRunner()


def invoke(*args: str):
    return runner.invoke(app, list(args), catch_exceptions=False)


def test_help_lists_complete_synthetic_lifecycle() -> None:
    result = invoke("--help")

    assert result.exit_code == 0
    assert "experiment" in result.stdout
    assert "paper" in result.stdout
    assert "data" not in result.stdout


def test_philosophy_validate_has_stable_json_output() -> None:
    result = invoke(
        "philosophy",
        "validate",
        str(PHILOSOPHY_DIR / "trend-v1.yaml"),
        "--format",
        "json",
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "philosophy.validate"
    assert payload["status"] == "ok"
    assert payload["result"]["name"] == "trend"
    assert len(payload["result"]["content_hash"]) == 64


def test_create_rejects_unsafe_run_id(tmp_path: Path) -> None:
    result = invoke(
        "experiment",
        "create",
        str(PHILOSOPHY_DIR / "trend-v1.yaml"),
        "--workspace",
        str(tmp_path),
        "--run-id",
        "../escape",
        "--start",
        "2024-01-05",
        "--end",
        "2024-02-02",
        "--format",
        "json",
    )

    assert result.exit_code == 3
    assert json.loads(result.stdout)["error"]["code"] == "invalid_run_id"


def test_paper_step_order_replay_and_evaluate_are_idempotent(tmp_path: Path) -> None:
    run_id = "trend-short"
    common = ("--workspace", str(tmp_path), "--run-id", run_id)
    created = invoke(
        "experiment",
        "create",
        str(PHILOSOPHY_DIR / "trend-v1.yaml"),
        *common,
        "--start",
        "2024-01-05",
        "--end",
        "2024-02-02",
    )
    assert created.exit_code == 0

    skipped = invoke("paper", "step", *common, "--session", "2024-01-12")
    assert skipped.exit_code == 5

    first = invoke("paper", "step", *common, "--session", "2024-01-05")
    assert first.exit_code == 0
    run_dir = tmp_path / run_id
    before = {path.name: path.read_bytes() for path in run_dir.iterdir()}

    repeated = invoke("paper", "step", *common, "--session", "2024-01-05")
    assert repeated.exit_code == 0
    assert before == {path.name: path.read_bytes() for path in run_dir.iterdir()}

    replayed = invoke("experiment", "replay", *common, "--format", "json")
    assert replayed.exit_code == 0
    result = json.loads(replayed.stdout)["result"]
    assert result["processed_sessions"] == 4
    assert result["skipped_sessions"] == 1

    evaluated = invoke("experiment", "evaluate", *common, "--format", "json")
    assert evaluated.exit_code == 0
    payload = json.loads(evaluated.stdout)["result"]
    assert payload["schema_version"] == 1
    assert "synthetic_mega_cap_proxy_relative" in payload["metrics"]


def test_compare_requires_two_runs(tmp_path: Path) -> None:
    result = invoke(
        "experiment",
        "compare",
        "--workspace",
        str(tmp_path),
        "--run-id",
        "only-one",
        "--format",
        "json",
    )

    assert result.exit_code == 3
    assert json.loads(result.stdout)["error"]["code"] == "comparison_requires_two_runs"
