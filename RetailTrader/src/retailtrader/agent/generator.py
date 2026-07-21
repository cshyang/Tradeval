"""Prepared-frame adapter and one-step deterministic agent execution."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from typing_extensions import Self

from retailtrader.agent.adjudication import AdjudicationResult, adjudicate_proposal
from retailtrader.agent.contracts import (
    CandidateSet,
    DecisionProposal,
    FrozenModel,
    HashString,
    MandateSpec,
    UtcSecond,
    canonical_hash,
)
from retailtrader.domain import ENGINE_VERSION, ExperimentManifest, MarketSnapshot
from retailtrader.simulation.frame import SimulationFrame
from retailtrader.simulation.runner import ExperimentRunner


class PreparedFrame(FrozenModel):
    schema_version: Literal[1]
    experiment_id: str
    candidate_set_hash: HashString
    decision: MarketSnapshot
    execution: MarketSnapshot
    execution_at: UtcSecond
    reference_equity: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    equal_weight_equity: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    reference_column: Literal[
        "synthetic_mega_cap_proxy_equity", "spy_equity"
    ] = "synthetic_mega_cap_proxy_equity"

    @model_validator(mode="after")
    def _valid_frame(self) -> Self:
        SimulationFrame(
            decision=self.decision,
            execution=self.execution,
            execution_at=self.execution_at,
        )
        return self

    @classmethod
    def from_frame(
        cls,
        *,
        experiment_id: str,
        candidate_set_hash: str,
        frame: SimulationFrame,
        reference_equity: str,
        equal_weight_equity: str,
        reference_column: str = "synthetic_mega_cap_proxy_equity",
    ) -> PreparedFrame:
        return cls.model_validate(
            {
                "schema_version": 1,
                "experiment_id": experiment_id,
                "candidate_set_hash": candidate_set_hash,
                "decision": frame.decision.model_dump(mode="json"),
                "execution": frame.execution.model_dump(mode="json"),
                "execution_at": frame.execution_at.isoformat().replace("+00:00", "Z"),
                "reference_equity": reference_equity,
                "equal_weight_equity": equal_weight_equity,
                "reference_column": reference_column,
            }
        )

    def to_frame(self) -> SimulationFrame:
        return SimulationFrame(
            decision=self.decision,
            execution=self.execution,
            execution_at=self.execution_at,
        )


class AgentStepResult(FrozenModel):
    status: Literal["committed", "no_op"]
    experiment_id: str
    session: str
    proposal_hash: HashString
    adjudication_hash: HashString
    proposal_path: str
    adjudication_path: str
    total_equity: str


def _read_model(path: Path, model_type):
    if not path.is_file():
        raise FileNotFoundError(path)
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def _immutable_json(path: Path, value: FrozenModel, conflict_label: str) -> None:
    content = (
        json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"conflicting immutable {conflict_label} for committed session")
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError(
                    f"conflicting immutable {conflict_label} for committed session"
                ) from None
        temporary.unlink()
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _manifest(
    mandate: MandateSpec,
    proposal: DecisionProposal,
) -> ExperimentManifest:
    end = mandate.horizon.end or proposal.decision_at.date()
    return ExperimentManifest(
        id=mandate.experiment_id,
        run_id=mandate.experiment_id,
        philosophy_name="agent",
        philosophy_version="v1",
        philosophy_hash=proposal.agent_protocol_hash,
        universe_hash=canonical_hash(mandate.universe),
        engine_version=ENGINE_VERSION,
        cadence=mandate.cadence,
        start=mandate.horizon.start,
        end=end,
        created_at=datetime.now(UTC),
        data_source="agent-prepared-frame-v1",
        benchmark_source="agent-prepared-reference-v1",
        initial_cash=Decimal(mandate.capital.initial_cash),
        slippage_bps=5,
    )


def _decision_record(
    proposal: DecisionProposal, adjudication: AdjudicationResult
) -> dict[str, object]:
    proposals = {decision.symbol: decision for decision in proposal.decisions}
    selected = []
    rejected = []
    for record in adjudication.records:
        if record.bounded_weight > 0:
            confidence = proposals.get(record.symbol).confidence if record.symbol in proposals else 0
            selected.append(
                {
                    "symbol": record.symbol,
                    "weight": record.bounded_weight,
                    "score": confidence,
                    "factors": [
                        {
                            "name": "agent_confidence",
                            "value": confidence,
                            "contribution": record.bounded_weight,
                        }
                    ],
                }
            )
        if record.disposition in {"rejected", "deferred"}:
            rejected.append(
                {
                    "symbol": record.symbol,
                    "reason": record.reason or record.disposition,
                    "score": None,
                }
            )
    return {
        "as_of": proposal.decision_at.isoformat().replace("+00:00", "Z"),
        "selected": selected,
        "rejected": rejected,
        "proposal_hash": adjudication.proposal_hash,
        "adjudication_hash": adjudication.adjudication_hash,
        "adjudication": [
            record.model_dump(mode="json") for record in adjudication.records
        ],
    }


def run_agent_step(workspace: Path, proposal_path: Path) -> AgentStepResult:
    """Validate sibling inputs, persist audit artifacts, and commit one transition."""
    workspace = Path(workspace)
    proposal_path = Path(proposal_path)
    step_dir = proposal_path.parent
    mandate = _read_model(step_dir / "mandate.json", MandateSpec)
    candidate_set = _read_model(step_dir / "candidate-set.json", CandidateSet)
    prepared = _read_model(step_dir / "prepared-frame.json", PreparedFrame)
    proposal = _read_model(proposal_path, DecisionProposal)
    if prepared.experiment_id != mandate.experiment_id:
        raise ValueError("prepared frame experiment_id does not match mandate")
    if prepared.candidate_set_hash != candidate_set.candidate_set_hash:
        raise ValueError("prepared frame candidate_set_hash does not match candidate set")

    frame = prepared.to_frame()
    run_dir = workspace / "run"
    manifest = _manifest(mandate, proposal)
    adjudication_holder: list[AdjudicationResult] = []

    def generate_target(experiment, snapshot):
        if not adjudication_holder:
            raise RuntimeError("adjudication was not prepared")
        result = adjudication_holder[0]
        return result.target, [_decision_record(proposal, result)]

    runner = ExperimentRunner(
        experiment=manifest,
        run_dir=run_dir,
        generate_target=generate_target,
        benchmarks={
            frame.execution_session: (
                Decimal(prepared.reference_equity),
                Decimal(prepared.equal_weight_equity),
            )
        },
        philosophy_yaml=f"agent_protocol_hash: {proposal.agent_protocol_hash}\n",
        max_turnover=mandate.limits.maximum_turnover,
        data_provenance={
            "kind": "agent_prepared",
            "mandate_hash": canonical_hash(mandate),
            "agent_protocol_hash": proposal.agent_protocol_hash,
        },
        reference_column=prepared.reference_column,
    )
    adjudication = adjudicate_proposal(
        proposal,
        candidate_set,
        mandate,
        runner.portfolio,
        frame,
    )
    adjudication_holder.append(adjudication)
    session = frame.execution_session.isoformat()
    audit_dir = workspace / "audit" / session
    persisted_proposal = audit_dir / "proposal.json"
    persisted_adjudication = audit_dir / "adjudication.json"
    _immutable_json(persisted_proposal, proposal, "proposal")
    _immutable_json(persisted_adjudication, adjudication, "adjudication")
    already_committed = runner.transition_store.path(session).exists()
    portfolio = runner.step(frame)
    return AgentStepResult(
        status="no_op" if already_committed else "committed",
        experiment_id=mandate.experiment_id,
        session=session,
        proposal_hash=adjudication.proposal_hash,
        adjudication_hash=adjudication.adjudication_hash,
        proposal_path=str(persisted_proposal),
        adjudication_path=str(persisted_adjudication),
        total_equity=str(portfolio.total_equity),
    )
