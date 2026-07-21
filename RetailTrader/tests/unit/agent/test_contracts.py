from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from retailtrader.agent.contracts import DecisionProposal, canonical_hash

FIXTURE = Path(__file__).parents[2] / "fixtures/agent/decision-proposal-v1.json"


def fixture_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_accepts_and_freezes_shared_wire_fixture() -> None:
    proposal = DecisionProposal.model_validate(fixture_payload())

    assert proposal.experiment_id == "exp-buffett-001"
    with pytest.raises(ValidationError, match="frozen"):
        proposal.experiment_id = "changed"  # type: ignore[misc]


def test_rejects_unknown_fields_at_every_object_boundary() -> None:
    payload = fixture_payload()
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DecisionProposal.model_validate(payload | {"unexpected": True})

    payload["decisions"][0]["unexpected"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DecisionProposal.model_validate(payload)


def test_rejects_duplicate_symbols() -> None:
    payload = fixture_payload()
    payload["decisions"].append(payload["decisions"][0].copy())

    with pytest.raises(ValidationError, match="duplicate symbol: AAPL"):
        DecisionProposal.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", -0.01),
        ("confidence", 1.01),
        ("desired_weight", -0.01),
        ("desired_weight", 1.01),
    ],
)
def test_rejects_weights_and_confidence_outside_unit_interval(
    field: str, value: float
) -> None:
    payload = fixture_payload()
    payload["decisions"][0][field] = value

    with pytest.raises(ValidationError):
        DecisionProposal.model_validate(payload)


def test_computes_frozen_cross_language_canonical_hash() -> None:
    proposal = DecisionProposal.model_validate(fixture_payload())

    assert canonical_hash(proposal) == (
        "sha256:e2ea7033cd2f4e073df346239c713baa3db662a5dc3ed61e549d7305c829b3df"
    )
