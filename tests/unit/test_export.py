from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from retailtrader.cli import EXPORT_ARTIFACTS, PHILOSOPHY_DIR, app

runner = CliRunner()


def invoke(*args: str):
    return runner.invoke(app, list(args), catch_exceptions=False)


def make_evaluated_run(workspace: Path) -> None:
    common = ("--workspace", str(workspace), "--run-id", "trend-short")
    assert invoke(
        "experiment",
        "create",
        str(PHILOSOPHY_DIR / "trend-v1.yaml"),
        *common,
        "--start",
        "2024-01-05",
        "--end",
        "2024-02-02",
    ).exit_code == 0
    assert invoke("experiment", "replay", *common).exit_code == 0
    assert invoke("experiment", "evaluate", *common).exit_code == 0


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_export_is_complete_atomic_and_byte_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "runs"
    out = tmp_path / "public" / "runs"
    make_evaluated_run(workspace)

    first = invoke(
        "export", "--workspace", str(workspace), "--out", str(out), "--format", "json"
    )
    assert first.exit_code == 0
    assert json.loads(first.stdout)["result"]["experiments"] == 1
    assert set(EXPORT_ARTIFACTS).issubset(
        {path.name for path in (out / "trend-short").iterdir()}
    )
    data = json.loads((out / "data.json").read_text(encoding="utf-8"))
    assert "synthetic_mega_cap_proxy" in data
    assert "spy" not in data
    assert data["experiments"][0]["rebalances"][0].get(
        "relative_to_synthetic_mega_cap_proxy"
    ) is not None

    before = tree_bytes(out)
    second = invoke("export", "--workspace", str(workspace), "--out", str(out))
    assert second.exit_code == 0
    assert tree_bytes(out) == before

    evaluation = workspace / "trend-short" / "evaluation.json"
    evaluation.write_text("not json", encoding="utf-8")
    failed = invoke("export", "--workspace", str(workspace), "--out", str(out))
    assert failed.exit_code == 3
    assert tree_bytes(out) == before


def test_export_rejects_output_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "runs"
    make_evaluated_run(workspace)

    result = invoke(
        "export", "--workspace", str(workspace), "--out", str(workspace / "public")
    )

    assert result.exit_code == 5
