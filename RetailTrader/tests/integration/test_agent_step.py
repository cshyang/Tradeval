from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from retailtrader.agent.contracts import (
    Candidate,
    CandidateSet,
    CapitalSpec,
    Decision,
    DecisionProposal,
    HorizonSpec,
    LimitSpec,
    MandateSpec,
    UniverseSpec,
    canonical_hash,
)
from retailtrader.agent.generator import PreparedFrame, run_agent_step
from tests.helpers import make_frame


def write_json(path: Path, value) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prepare_step(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "experiment"
    step_dir = tmp_path / "step"
    workspace.mkdir()
    step_dir.mkdir()
    mandate = MandateSpec(
        schema_version=1,
        experiment_id="exp-agent-step",
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
        horizon=HorizonSpec(kind="hindsight", start=date(2025, 1, 1), end=date(2025, 3, 1)),
        limits=LimitSpec(
            minimum_cash_weight=0.05,
            maximum_position_weight=0.12,
            maximum_turnover=0.02,
            maximum_drawdown=0.25,
        ),
    )
    candidate_payload = {
        "schema_version": 1,
        "experiment_id": mandate.experiment_id,
        "screener": "price_quality_v1",
        "decision_at": "2025-01-31T20:00:00Z",
        "market_data_hash": "sha256:" + "a" * 64,
        "candidates": [
            Candidate(
                symbol="AAPL",
                score=1,
                evidence_coverage=1,
                price_history_sessions=300,
                average_dollar_volume="50000000",
                latest_price="100",
                metrics=(),
            ).model_dump(mode="json")
        ],
        "exclusions": [],
    }
    candidates = CandidateSet.model_validate(
        candidate_payload
        | {"candidate_set_hash": canonical_hash(candidate_payload)}
    )
    proposal = DecisionProposal(
        schema_version=1,
        experiment_id=mandate.experiment_id,
        decision_at="2025-01-31T20:00:00Z",
        candidate_set_hash=candidates.candidate_set_hash,
        agent_protocol_hash="sha256:" + "b" * 64,
        decisions=(
            Decision(
                symbol="AAPL",
                stance="buy",
                confidence=0.9,
                desired_weight=0.18,
                thesis="quality compounder",
                evidence_refs=("obs:AAPL",),
                risks=("valuation",),
                invalidating_conditions=("cash flow weakens",),
                intended_holding_period="3-5 years",
            ),
        ),
        abstentions=(),
    )
    prepared = PreparedFrame.from_frame(
        experiment_id=mandate.experiment_id,
        candidate_set_hash=candidates.candidate_set_hash,
        frame=make_frame(
            date(2025, 1, 31),
            date(2025, 2, 3),
            {"AAPL": ("99", "100")},
            {"AAPL": ("101", "102")},
        ),
        reference_equity="100500.00",
        equal_weight_equity="100250.00",
    )
    write_json(step_dir / "mandate.json", mandate)
    write_json(step_dir / "candidate-set.json", candidates)
    write_json(step_dir / "prepared-frame.json", prepared)
    proposal_path = step_dir / "decision-proposal.json"
    write_json(proposal_path, proposal)
    return workspace, proposal_path


def all_file_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_agent_step_is_idempotent_and_persists_proposal_hashes(tmp_path: Path) -> None:
    workspace, proposal_path = prepare_step(tmp_path)

    first = run_agent_step(workspace, proposal_path)
    before = all_file_bytes(workspace)
    second = run_agent_step(workspace, proposal_path)

    assert first.status == "committed"
    assert second.status == "no_op"
    assert all_file_bytes(workspace) == before
    journal = json.loads(next((workspace / "run/transitions").glob("*.json")).read_text())
    decision = journal["events"][0]["payload"]["decisions"][0]
    assert decision["proposal_hash"] == first.proposal_hash
    assert decision["adjudication_hash"] == first.adjudication_hash
    assert any(
        event["event_type"] == "order_rejected"
        and event["payload"]["reason"] == "max turnover"
        for event in journal["events"]
    )


def test_agent_step_rejects_conflicting_content_for_committed_session(tmp_path: Path) -> None:
    workspace, proposal_path = prepare_step(tmp_path)
    run_agent_step(workspace, proposal_path)
    payload = json.loads(proposal_path.read_text())
    payload["decisions"][0]["desired_weight"] = 0.05
    conflicting = proposal_path.with_name("conflicting-proposal.json")
    write_json(conflicting, payload)

    with pytest.raises(ValueError, match="conflicting immutable proposal"):
        run_agent_step(workspace, conflicting)


def test_agent_step_rejects_candidate_identity_mismatch(tmp_path: Path) -> None:
    workspace, proposal_path = prepare_step(tmp_path)
    payload = json.loads(proposal_path.read_text())
    payload["candidate_set_hash"] = "sha256:" + "f" * 64
    write_json(proposal_path, payload)

    with pytest.raises(ValueError, match="candidate_set_hash"):
        run_agent_step(workspace, proposal_path)
