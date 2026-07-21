"""Convert untrusted AI portfolio intent into deterministic bounded targets."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from retailtrader.agent.contracts import (
    CandidateSet,
    DecisionProposal,
    FrozenModel,
    HashString,
    MandateSpec,
    UtcSecond,
    canonical_hash,
)
from retailtrader.domain import (
    PortfolioSnapshot,
    TargetPortfolio,
    TargetPosition,
)
from retailtrader.simulation.frame import SimulationFrame


class AdjudicationRecord(FrozenModel):
    symbol: str
    stance: Literal["buy", "hold", "sell"]
    requested_weight: float
    bounded_weight: float
    disposition: Literal["accepted", "capped", "rejected", "deferred"]
    reason: str | None


class AdjudicationResult(FrozenModel):
    schema_version: Literal[1]
    experiment_id: str
    decision_at: UtcSecond
    candidate_set_hash: HashString
    proposal_hash: HashString
    records: tuple[AdjudicationRecord, ...]
    target: TargetPortfolio
    adjudication_hash: HashString


def _validate_candidate_hash(candidate_set: CandidateSet) -> None:
    payload = candidate_set.model_dump(
        mode="python", exclude={"candidate_set_hash"}
    )
    if canonical_hash(payload) != candidate_set.candidate_set_hash:
        raise ValueError("candidate_set_hash does not match candidate content")


def _current_weights(
    portfolio: PortfolioSnapshot, frame: SimulationFrame
) -> dict[str, Decimal]:
    decision_prices = {bar.symbol: bar.close for bar in frame.decision.bars}
    equity = portfolio.cash + sum(
        (
            Decimal(position.quantity)
            * decision_prices.get(position.symbol, position.price)
            for position in portfolio.positions
        ),
        Decimal(0),
    )
    if equity <= 0:
        raise ValueError("portfolio equity must be positive")
    return {
        position.symbol: (
            Decimal(position.quantity)
            * decision_prices.get(position.symbol, position.price)
            / equity
        )
        for position in portfolio.positions
    }


def adjudicate_proposal(
    proposal: DecisionProposal,
    candidate_set: CandidateSet,
    mandate: MandateSpec,
    portfolio: PortfolioSnapshot,
    frame: SimulationFrame,
) -> AdjudicationResult:
    """Apply identity, position, data-availability, and cash constraints."""
    _validate_candidate_hash(candidate_set)
    if proposal.experiment_id != mandate.experiment_id:
        raise ValueError("proposal experiment_id does not match mandate")
    if candidate_set.experiment_id != mandate.experiment_id:
        raise ValueError("candidate set experiment_id does not match mandate")
    if proposal.candidate_set_hash != candidate_set.candidate_set_hash:
        raise ValueError("proposal candidate_set_hash does not match candidate set")
    if proposal.decision_at != candidate_set.decision_at:
        raise ValueError("proposal decision_at does not match candidate set")
    if proposal.decision_at != frame.decision.as_of:
        raise ValueError("proposal decision_at does not match prepared frame")
    if portfolio.run_id != mandate.experiment_id:
        raise ValueError("portfolio run_id does not match experiment")

    candidate_symbols = {candidate.symbol for candidate in candidate_set.candidates}
    execution_symbols = {bar.symbol for bar in frame.execution.bars}
    current_weights = _current_weights(portfolio, frame)
    maximum_position = Decimal(str(mandate.limits.maximum_position_weight))
    requested_weights: dict[str, Decimal] = {}
    records: dict[str, AdjudicationRecord] = {}

    for decision in sorted(proposal.decisions, key=lambda item: item.symbol):
        requested = Decimal(str(decision.desired_weight))
        current = current_weights.get(decision.symbol, Decimal(0))
        if decision.symbol not in candidate_symbols:
            records[decision.symbol] = AdjudicationRecord(
                symbol=decision.symbol,
                stance=decision.stance,
                requested_weight=decision.desired_weight,
                bounded_weight=float(current),
                disposition="rejected",
                reason="symbol is outside candidate set",
            )
            if current > 0:
                requested_weights[decision.symbol] = current
            continue
        if decision.symbol not in execution_symbols:
            records[decision.symbol] = AdjudicationRecord(
                symbol=decision.symbol,
                stance=decision.stance,
                requested_weight=decision.desired_weight,
                bounded_weight=float(current),
                disposition="deferred",
                reason="missing execution bar",
            )
            if current > 0:
                requested_weights[decision.symbol] = current
            continue
        bounded = min(requested, maximum_position)
        requested_weights[decision.symbol] = bounded
        capped = bounded != requested
        records[decision.symbol] = AdjudicationRecord(
            symbol=decision.symbol,
            stance=decision.stance,
            requested_weight=decision.desired_weight,
            bounded_weight=float(bounded),
            disposition="capped" if capped else "accepted",
            reason="maximum position weight" if capped else None,
        )

    for abstention in sorted(proposal.abstentions, key=lambda item: item.symbol):
        current = current_weights.get(abstention.symbol, Decimal(0))
        if current > 0:
            requested_weights[abstention.symbol] = min(current, maximum_position)
        records[abstention.symbol] = AdjudicationRecord(
            symbol=abstention.symbol,
            stance="hold",
            requested_weight=float(current),
            bounded_weight=float(min(current, maximum_position)),
            disposition="deferred",
            reason="agent abstained",
        )

    mentioned = set(records)
    for symbol, current in sorted(current_weights.items()):
        if symbol in mentioned:
            continue
        bounded = min(current, maximum_position)
        requested_weights[symbol] = bounded
        records[symbol] = AdjudicationRecord(
            symbol=symbol,
            stance="hold",
            requested_weight=float(current),
            bounded_weight=float(bounded),
            disposition="capped" if bounded != current else "deferred",
            reason=(
                "maximum position weight"
                if bounded != current
                else "no proposal; existing position preserved"
            ),
        )

    maximum_invested = Decimal(1) - Decimal(
        str(mandate.limits.minimum_cash_weight)
    )
    requested_total = sum(requested_weights.values(), Decimal(0))
    if requested_total > maximum_invested:
        scale = maximum_invested / requested_total
        for symbol in sorted(requested_weights):
            requested_weights[symbol] *= scale
            record = records[symbol]
            records[symbol] = record.model_copy(
                update={
                    "bounded_weight": float(requested_weights[symbol]),
                    "disposition": "capped",
                    "reason": "minimum cash buffer",
                }
            )

    positions = tuple(
        TargetPosition(symbol=symbol, weight=float(weight))
        for symbol, weight in sorted(requested_weights.items())
        if weight > 0
    )
    invested = sum((Decimal(str(position.weight)) for position in positions), Decimal(0))
    target = TargetPortfolio(
        run_id=portfolio.run_id,
        as_of=frame.decision.as_of,
        cash_weight=float(Decimal(1) - invested),
        positions=positions,
    )
    proposal_hash = canonical_hash(proposal)
    payload = {
        "schema_version": 1,
        "experiment_id": mandate.experiment_id,
        "decision_at": frame.decision.as_of.isoformat().replace("+00:00", "Z"),
        "candidate_set_hash": candidate_set.candidate_set_hash,
        "proposal_hash": proposal_hash,
        "records": [
            record.model_dump(mode="python")
            for record in sorted(records.values(), key=lambda item: item.symbol)
        ],
        "target": target.model_dump(mode="python"),
    }
    return AdjudicationResult.model_validate(
        payload | {"adjudication_hash": canonical_hash(payload)}
    )
