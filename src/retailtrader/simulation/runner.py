"""Unified replay / forward-paper runner.

Both modes call the single module-level `step` transition (Core Invariant 4):
historical replay loops it over frames; forward paper trading calls it once
per completed decision/execution pair. Target generation is injected as a callable, so
the runner never imports scoring or allocation.

Idempotency: a session whose `rebalance_completed` event already exists in the
event log is skipped without writing anything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from retailtrader.domain import (
    ExperimentManifest,
    MarketSnapshot,
    PortfolioSnapshot,
    Position,
    TargetPortfolio,
)
from retailtrader.simulation.execution import execute_rebalance
from retailtrader.simulation.frame import SimulationFrame
from retailtrader.storage.artifacts import RunWriter, portfolio_row
from retailtrader.storage.events import EventLog

TargetGenerator = Callable[
    [ExperimentManifest, MarketSnapshot],
    tuple[TargetPortfolio, list[dict[str, Any]]],
]
Benchmarks = Mapping[date, tuple[Decimal, Decimal]]


def initial_portfolio(
    experiment: ExperimentManifest, cash: Decimal, as_of: datetime
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        run_id=experiment.run_id,
        as_of=as_of,
        cash=cash,
        positions=(),
        total_equity=cash,
    )


def portfolio_from_row(run_id: str, row: Mapping[str, Any]) -> PortfolioSnapshot:
    positions = tuple(
        Position(
            symbol=item["symbol"],
            quantity=int(item["quantity"]),
            price=Decimal(item["price"]),
            value=Decimal(item["value"]),
        )
        for item in row["positions"]
    )
    return PortfolioSnapshot(
        run_id=run_id,
        as_of=datetime.fromisoformat(row["as_of"]),
        cash=Decimal(row["cash"]),
        positions=positions,
        total_equity=Decimal(row["total_equity"]),
    )


def step(
    experiment: ExperimentManifest,
    portfolio: PortfolioSnapshot,
    frame: SimulationFrame,
    generate_target: TargetGenerator,
    *,
    event_log: EventLog,
    writer: RunWriter,
    benchmarks: Benchmarks,
    slippage_bps: int = 0,
) -> PortfolioSnapshot:
    """Single portfolio transition shared by replay and forward paper modes."""
    session_key = frame.execution.as_of.astimezone(UTC).isoformat()
    if session_key in event_log.completed_sessions():
        return portfolio

    session = frame.execution_session
    if session not in benchmarks:
        raise ValueError(f"missing benchmark equity for session {session}")
    spy_equity, equal_weight_equity = benchmarks[session]

    target, decisions = generate_target(experiment, frame.decision)
    if target.as_of != frame.decision.as_of:
        raise ValueError("target.as_of must equal frame.decision.as_of")
    event_log.append("target_generated", frame.decision.as_of, target.model_dump())
    for record in decisions:
        writer.append_decision(record)

    result = execute_rebalance(
        portfolio,
        target,
        frame.execution,
        filled_at=frame.execution_at,
        slippage_bps=slippage_bps,
    )

    for order in result.orders:
        event_log.append("order_created", frame.execution_at, order.model_dump())
        writer.append_order(
            {
                "as_of": frame.execution_at,
                "symbol": order.symbol,
                "side": order.side,
                "quantity": order.quantity,
                "status": "created",
                "reason": None,
            }
        )
    for rejection in result.rejections:
        payload = {
            "as_of": frame.execution_at,
            "symbol": rejection.symbol,
            "side": rejection.side,
            "quantity": rejection.requested_quantity,
            "status": "rejected",
            "reason": rejection.reason,
        }
        event_log.append("order_rejected", frame.execution_at, payload)
        writer.append_order(payload)
    for fill in result.fills:
        event_log.append("order_filled", frame.execution_at, fill.model_dump())
        writer.append_fill(fill)

    event_log.append("portfolio_marked", frame.execution.as_of, portfolio_row(result.portfolio))
    writer.append_portfolio(result.portfolio)
    writer.append_equity_row(
        session, result.portfolio.total_equity, spy_equity, equal_weight_equity
    )
    event_log.append("rebalance_completed", frame.execution.as_of, {"session": session})
    return result.portfolio


class ExperimentRunner:
    """Drives one experiment run directory in replay or forward paper mode."""

    def __init__(
        self,
        *,
        experiment: ExperimentManifest,
        run_dir: Path,
        generate_target: TargetGenerator,
        benchmarks: Benchmarks,
        philosophy_yaml: str,
        initial_cash: Decimal,
        slippage_bps: int = 0,
    ) -> None:
        self.experiment = experiment
        self.generate_target = generate_target
        self.benchmarks = benchmarks
        self.slippage_bps = slippage_bps
        self.writer = RunWriter(run_dir)
        self.event_log = EventLog(run_dir / "events.jsonl", experiment.run_id)

        events = self.event_log.read()
        if not events:
            self.writer.write_manifest(experiment)
            self.writer.write_philosophy(philosophy_yaml)
            created_as_of = datetime.combine(experiment.start, time(0), tzinfo=UTC)
            self.portfolio = initial_portfolio(experiment, initial_cash, created_as_of)
            self.event_log.append(
                "portfolio_created",
                created_as_of,
                {"cash": initial_cash, "as_of": created_as_of},
            )
        else:
            self.portfolio = self._restore(events, initial_cash)

    def _restore(
        self, events: Sequence[Mapping[str, Any]], initial_cash: Decimal
    ) -> PortfolioSnapshot:
        marked = [event for event in events if event["event_type"] == "portfolio_marked"]
        if marked:
            return portfolio_from_row(self.experiment.run_id, marked[-1]["payload"])
        created_as_of = datetime.combine(self.experiment.start, time(0), tzinfo=UTC)
        return initial_portfolio(self.experiment, initial_cash, created_as_of)

    def step(self, frame: SimulationFrame) -> PortfolioSnapshot:
        """Forward paper mode: process one completed decision/execution frame."""
        self.portfolio = step(
            self.experiment,
            self.portfolio,
            frame,
            self.generate_target,
            event_log=self.event_log,
            writer=self.writer,
            benchmarks=self.benchmarks,
            slippage_bps=self.slippage_bps,
        )
        return self.portfolio

    def replay(self, frames: Sequence[SimulationFrame]) -> PortfolioSnapshot:
        """Historical replay mode: loop the same transition over all frames."""
        for frame in sorted(frames, key=lambda item: item.execution.as_of):
            self.step(frame)
        return self.portfolio
