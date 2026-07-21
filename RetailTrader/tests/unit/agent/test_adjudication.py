from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

from retailtrader.agent.adjudication import adjudicate_proposal
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
from retailtrader.domain import MarketBar, MarketSnapshot, PortfolioSnapshot
from retailtrader.simulation.execution import execute_rebalance
from retailtrader.simulation.frame import SimulationFrame

CUTOFF = datetime(2025, 1, 31, 20, tzinfo=UTC)


def make_mandate(*, max_position: float = 0.12, cash: float = 0.05) -> MandateSpec:
    return MandateSpec(
        schema_version=1,
        experiment_id="exp-agent-step",
        capital=CapitalSpec(currency="USD", initial_cash="100000.00"),
        market="US",
        universe=UniverseSpec(
            symbols=("AAPL", "MSFT", "NVDA"),
            screener="price_quality_v1",
            max_candidates=3,
            minimum_history_sessions=1,
            minimum_average_dollar_volume="1",
            minimum_evidence_coverage=0,
            pinned_symbols=(),
            excluded_symbols=(),
        ),
        cadence="monthly",
        horizon=HorizonSpec(kind="hindsight", start=date(2025, 1, 1), end=date(2025, 3, 1)),
        limits=LimitSpec(
            minimum_cash_weight=cash,
            maximum_position_weight=max_position,
            maximum_turnover=0.20,
            maximum_drawdown=0.25,
        ),
    )


def make_candidates() -> CandidateSet:
    candidates = [
        Candidate(
            symbol=symbol,
            score=1.0,
            evidence_coverage=1.0,
            price_history_sessions=300,
            average_dollar_volume="50000000",
            latest_price="100",
            metrics=(),
        ).model_dump(mode="json")
        for symbol in ("AAPL", "MSFT", "NVDA")
    ]
    payload = {
        "schema_version": 1,
        "experiment_id": "exp-agent-step",
        "screener": "price_quality_v1",
        "decision_at": "2025-01-31T20:00:00Z",
        "market_data_hash": "sha256:" + "a" * 64,
        "candidates": candidates,
        "exclusions": [],
    }
    return CandidateSet.model_validate(
        payload | {"candidate_set_hash": canonical_hash(payload)}
    )


def make_decision(symbol: str, weight: float, stance: str = "buy") -> Decision:
    return Decision(
        symbol=symbol,
        stance=stance,
        confidence=0.8,
        desired_weight=weight,
        thesis=f"thesis for {symbol}",
        evidence_refs=(f"obs:{symbol}",),
        risks=("valuation",),
        invalidating_conditions=("cash flow weakens",),
        intended_holding_period="3-5 years",
    )


def make_proposal(*decisions: Decision) -> DecisionProposal:
    return DecisionProposal(
        schema_version=1,
        experiment_id="exp-agent-step",
        decision_at="2025-01-31T20:00:00Z",
        candidate_set_hash=make_candidates().candidate_set_hash,
        agent_protocol_hash="sha256:" + "b" * 64,
        decisions=decisions,
        abstentions=(),
    )


def frame(*, include_msft_execution: bool = False):
    execution = {"AAPL": ("100", "102"), "NVDA": ("100", "98")}
    if include_msft_execution:
        execution["MSFT"] = ("100", "101")
    def snapshot(session: date, prices: dict[str, tuple[str, str]]) -> MarketSnapshot:
        return MarketSnapshot(
            as_of=datetime.combine(session, time(20), tzinfo=UTC),
            bars=tuple(
                MarketBar(
                    symbol=symbol,
                    session=session,
                    open=Decimal(open_price),
                    high=max(Decimal(open_price), Decimal(close_price)),
                    low=min(Decimal(open_price), Decimal(close_price)),
                    close=Decimal(close_price),
                    volume=1_000,
                )
                for symbol, (open_price, close_price) in sorted(prices.items())
            ),
        )

    return SimulationFrame(
        decision=snapshot(
            date(2025, 1, 31),
            {
                "AAPL": ("99", "100"),
                "MSFT": ("99", "100"),
                "NVDA": ("99", "100"),
            },
        ),
        execution=snapshot(date(2025, 2, 3), execution),
        execution_at=datetime(2025, 2, 3, 14, 30, tzinfo=UTC),
    )


def portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        run_id="exp-agent-step",
        as_of=datetime(2025, 1, 30, 20, tzinfo=UTC),
        cash=Decimal("100000.00"),
        positions=(),
        total_equity=Decimal("100000.00"),
    )


def test_accepts_caps_rejects_and_defers_proposals() -> None:
    result = adjudicate_proposal(
        make_proposal(
            make_decision("AAPL", 0.08),
            make_decision("NVDA", 0.18),
            make_decision("MSFT", 0.10),
            make_decision("XYZ", 0.10),
        ),
        make_candidates(),
        make_mandate(),
        portfolio(),
        frame(),
    )

    records = {record.symbol: record for record in result.records}
    assert (records["AAPL"].disposition, records["AAPL"].bounded_weight) == (
        "accepted",
        0.08,
    )
    assert (records["NVDA"].disposition, records["NVDA"].bounded_weight) == (
        "capped",
        0.12,
    )
    assert records["MSFT"].disposition == "deferred"
    assert records["MSFT"].reason == "missing execution bar"
    assert records["XYZ"].disposition == "rejected"
    assert records["XYZ"].reason == "symbol is outside candidate set"
    assert result.target.cash_weight == 0.8
    assert result.proposal_hash.startswith("sha256:")
    assert result.adjudication_hash.startswith("sha256:")


def test_cash_buffer_scales_requested_weights_deterministically() -> None:
    result = adjudicate_proposal(
        make_proposal(make_decision("AAPL", 0.7), make_decision("NVDA", 0.7)),
        make_candidates(),
        make_mandate(max_position=0.8, cash=0.2),
        portfolio(),
        frame(),
    )

    records = {record.symbol: record for record in result.records}
    assert records["AAPL"].bounded_weight == records["NVDA"].bounded_weight == 0.4
    assert records["AAPL"].reason == records["NVDA"].reason == "minimum cash buffer"
    assert result.target.cash_weight == 0.2


def test_shared_execution_layer_enforces_turnover_after_adjudication() -> None:
    prepared_frame = frame(include_msft_execution=True)
    result = adjudicate_proposal(
        make_proposal(make_decision("AAPL", 0.4), make_decision("NVDA", 0.4)),
        make_candidates(),
        make_mandate(max_position=0.8, cash=0.2),
        portfolio(),
        prepared_frame,
    )

    execution = execute_rebalance(
        portfolio(),
        result.target,
        prepared_frame.execution,
        filled_at=prepared_frame.execution_at,
        max_turnover=0.20,
    )

    assert any(rejection.reason == "max turnover" for rejection in execution.rejections)
