"""Deterministic synthetic inputs shared by simulation and evaluation tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from retailtrader.domain import (
    ExperimentManifest,
    MarketBar,
    MarketSnapshot,
    TargetPortfolio,
    TargetPosition,
)
from retailtrader.simulation.frame import SimulationFrame

RUN_ID = "run-test"


def close_dt(session: date) -> datetime:
    return datetime.combine(session, time(20), tzinfo=UTC)


def open_dt(session: date) -> datetime:
    return datetime.combine(session, time(14, 30), tzinfo=UTC)


def make_experiment(run_id: str = RUN_ID) -> ExperimentManifest:
    return ExperimentManifest(
        id=run_id,
        run_id=run_id,
        philosophy_name="stub",
        philosophy_version="v1",
        philosophy_hash="stub-hash",
        universe_hash="stub-universe",
        cadence="weekly",
        start=date(2024, 1, 1),
        end=date(2024, 2, 1),
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def make_snapshot(session: date, prices: dict[str, tuple[str, str]]) -> MarketSnapshot:
    """Build a snapshot from {symbol: (open, close)} decimal strings."""
    bars = tuple(
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
    )
    return MarketSnapshot(as_of=close_dt(session), bars=bars)


def make_frame(
    decision_session: date,
    execution_session: date,
    decision_prices: dict[str, tuple[str, str]],
    execution_prices: dict[str, tuple[str, str]],
) -> SimulationFrame:
    return SimulationFrame(
        decision=make_snapshot(decision_session, decision_prices),
        execution=make_snapshot(execution_session, execution_prices),
        execution_at=open_dt(execution_session),
    )


def stub_generator(
    experiment: ExperimentManifest, snapshot: MarketSnapshot
) -> tuple[TargetPortfolio, list[dict[str, Any]]]:
    """Deterministic stand-in for scoring/allocation: rotates a 2-symbol pick."""
    symbols = sorted(bar.symbol for bar in snapshot.bars)
    offset = snapshot.as_of.date().toordinal() % 2
    selected = symbols[offset : offset + 2]
    weight = 0.475
    target = TargetPortfolio(
        run_id=experiment.run_id,
        as_of=snapshot.as_of,
        cash_weight=0.05,
        positions=tuple(TargetPosition(symbol=symbol, weight=weight) for symbol in selected),
    )
    record = {
        "as_of": snapshot.as_of.isoformat(),
        "selected": [
            {
                "symbol": symbol,
                "weight": weight,
                "score": round(1.0 - index * 0.1, 4),
                "factors": [{"name": "stub_factor", "value": 1.0, "contribution": weight}],
            }
            for index, symbol in enumerate(selected)
        ],
        "rejected": [
            {"symbol": symbol, "reason": "score below cutoff", "score": 0.1}
            for symbol in symbols
            if symbol not in selected
        ],
    }
    return target, [record]
