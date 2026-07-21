from __future__ import annotations

import json
from types import SimpleNamespace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

import retailtrader.cli as cli_module
from retailtrader.agent.contracts import Candidate, CandidateSet, CapitalSpec, HorizonSpec, LimitSpec, MandateSpec, UniverseSpec, canonical_hash
from retailtrader.agent.evidence import EvidenceMetric
from retailtrader.agent.generator import AgentStepResult
from retailtrader.agent.screening import ScreeningInput
from retailtrader.cli import PHILOSOPHY_DIR, app

runner = CliRunner()


def invoke(*args: str):
    return runner.invoke(app, list(args), catch_exceptions=False)


def test_help_lists_synthetic_lifecycle_and_opt_in_market_replay() -> None:
    result = invoke("--help")

    assert result.exit_code == 0
    assert "experiment" in result.stdout
    assert "paper" in result.stdout
    assert "market-replay" in result.stdout


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
    before = {
        str(path.relative_to(run_dir)): path.read_bytes()
        for path in run_dir.rglob("*")
        if path.is_file()
    }

    repeated = invoke("paper", "step", *common, "--session", "2024-01-05")
    assert repeated.exit_code == 0
    assert before == {
        str(path.relative_to(run_dir)): path.read_bytes()
        for path in run_dir.rglob("*")
        if path.is_file()
    }

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


def test_agent_candidates_writes_stable_json_artifact(
    tmp_path: Path, monkeypatch
) -> None:
    cutoff = datetime(2025, 1, 31, 21, tzinfo=UTC)
    mandate = MandateSpec(
        schema_version=1,
        experiment_id="exp-cli-screen",
        capital=CapitalSpec(currency="USD", initial_cash="100000.00"),
        market="US",
        universe=UniverseSpec(
            symbols=("AAPL",),
            screener="price_quality_v1",
            max_candidates=1,
            minimum_history_sessions=1,
            minimum_average_dollar_volume="1",
            minimum_evidence_coverage=0,
            pinned_symbols=(),
            excluded_symbols=(),
        ),
        cadence="monthly",
        horizon=HorizonSpec(kind="hindsight", start=date(2024, 1, 1), end=date(2025, 1, 31)),
        limits=LimitSpec(
            minimum_cash_weight=0.05,
            maximum_position_weight=0.12,
            maximum_turnover=0.2,
            maximum_drawdown=0.25,
        ),
    )
    mandate_path = tmp_path / "mandate.json"
    mandate_path.write_text(mandate.model_dump_json(indent=2), encoding="utf-8")
    metric = EvidenceMetric(
        name="earnings_consistency",
        value=Decimal("1"),
        source_observation_ids=("obs:earnings",),
        formula_version="earnings_v1",
        decision_cutoff=cutoff,
        unavailable_reason=None,
    )
    prepared = (
        ScreeningInput(
            symbol="AAPL",
            supported_security=True,
            price_history_sessions=300,
            average_dollar_volume=Decimal("50000000"),
            latest_price=Decimal("190"),
            metrics=(metric,),
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "prepare_screening_inputs",
        lambda *args, **kwargs: (prepared, "sha256:" + "a" * 64),
    )
    out = tmp_path / "candidate-set.json"
    command = (
        "agent",
        "candidates",
        "--experiment",
        str(mandate_path),
        "--decision-at",
        "2025-01-31T21:00:00Z",
        "--out",
        str(out),
        "--format",
        "json",
    )

    first = invoke(*command)
    assert first.exit_code == 0, first.stdout
    first_bytes = out.read_bytes()
    second = invoke(*command)

    assert first.exit_code == second.exit_code == 0
    assert out.read_bytes() == first_bytes
    payload = json.loads(first.stdout)
    assert payload["command"] == "agent.candidates"
    assert payload["result"]["candidate_count"] == 1
    assert json.loads(first_bytes)["candidates"][0]["symbol"] == "AAPL"

    workspace = tmp_path / "hindsight"
    prepared = invoke(
        "agent", "prepare-hindsight",
        "--experiment", str(mandate_path),
        "--workspace", str(workspace),
        "--format", "json",
    )
    assert prepared.exit_code == 0, prepared.stdout
    frame = json.loads(prepared.stdout)["result"]["frames"][0]
    step_dir = Path(frame["step_directory"])
    sealed = invoke(
        "agent", "prepare-frame",
        "--source", str(step_dir / "frame-source.json"),
        "--candidate-set", str(out),
        "--out", str(step_dir / "prepared-frame.json"),
        "--format", "json",
    )
    assert sealed.exit_code == 0, sealed.stdout
    assert json.loads((step_dir / "prepared-frame.json").read_text())["candidate_set_hash"] == payload["result"]["candidate_set_hash"]


def test_agent_step_returns_stable_json_envelope(tmp_path: Path, monkeypatch) -> None:
    proposal = tmp_path / "decision-proposal.json"
    proposal.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        cli_module,
        "run_agent_step",
        lambda workspace, proposal_path: AgentStepResult(
            status="committed",
            experiment_id="exp-agent-step",
            session="2025-02-03",
            proposal_hash="sha256:" + "a" * 64,
            adjudication_hash="sha256:" + "b" * 64,
            proposal_path=str(proposal_path),
            adjudication_path=str(workspace / "audit/adjudication.json"),
            total_equity="100100.00",
        ),
    )

    result = invoke(
        "agent",
        "step",
        "--workspace",
        str(tmp_path),
        "--proposal",
        str(proposal),
        "--format",
        "json",
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "agent.step"
    assert payload["result"]["status"] == "committed"
    assert payload["result"]["session"] == "2025-02-03"


def test_finalize_hindsight_builds_deterministic_quality_control(tmp_path: Path, monkeypatch) -> None:
    step = tmp_path / "prepared" / "2025-02-03"
    step.mkdir(parents=True)
    mandate = MandateSpec(
        schema_version=1,
        experiment_id="exp-control",
        capital=CapitalSpec(currency="USD", initial_cash="100000.00"),
        market="US",
        universe=UniverseSpec(symbols=("AAPL",), screener="price_quality_v1", max_candidates=1, minimum_history_sessions=1, minimum_average_dollar_volume="1", minimum_evidence_coverage=0, pinned_symbols=(), excluded_symbols=()),
        cadence="monthly",
        horizon=HorizonSpec(kind="hindsight", start=date(2025, 1, 1), end=date(2025, 4, 1)),
        limits=LimitSpec(minimum_cash_weight=0.1, maximum_position_weight=0.12, maximum_turnover=0.2, maximum_drawdown=0.25),
    )
    candidate_payload = {
        "schema_version": 1, "experiment_id": mandate.experiment_id,
        "screener": "price_quality_v1", "decision_at": "2025-01-31T20:00:00Z",
        "market_data_hash": "sha256:" + "a" * 64,
        "candidates": [Candidate(symbol="AAPL", score=1, evidence_coverage=1, price_history_sessions=300, average_dollar_volume="1", latest_price="100", metrics=()).model_dump(mode="json")],
        "exclusions": [],
    }
    candidates = CandidateSet.model_validate(candidate_payload | {"candidate_set_hash": canonical_hash(candidate_payload)})
    (step / "mandate.json").write_text(mandate.model_dump_json(), encoding="utf-8")
    (step / "candidate-set.json").write_text(candidates.model_dump_json(), encoding="utf-8")
    manifest = SimpleNamespace(run_id="exp-control", initial_cash=Decimal("100000.00"))
    monkeypatch.setattr(cli_module, "read_manifest", lambda path: manifest)
    monkeypatch.setattr(cli_module, "_evaluate_run", lambda *args: object())
    monkeypatch.setattr(cli_module, "write_comparison_md", lambda *args: None)
    proposals: list[Path] = []
    monkeypatch.setattr(cli_module, "run_agent_step", lambda workspace, path: proposals.append(path))

    result = invoke("agent", "finalize-hindsight", "--workspace", str(tmp_path), "--format", "json")

    assert result.exit_code == 0, result.stdout
    proposal = json.loads(proposals[0].read_text())
    assert proposal["decisions"][0]["thesis"] == "deterministic price-quality control"
    assert json.loads((tmp_path / "controls.json").read_text())["cash"] == "100000.00"
