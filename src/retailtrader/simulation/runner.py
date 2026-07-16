"""Unified replay / forward-paper runner.

Both modes call the single module-level `step` transition. Completed transitions
are owned by immutable per-session journals; public JSONL/CSV files are only
atomic, deterministic projections of those journals.
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
from retailtrader.storage.artifacts import RunWriter, fill_row, portfolio_row
from retailtrader.storage.events import EventLog, event_record, to_jsonable
from retailtrader.storage.transitions import FailureHook, TransitionStore

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


def _materialize(
    event_log: EventLog,
    writer: RunWriter,
    transition_store: TransitionStore,
    initial_events: Sequence[Mapping[str, Any]],
    failure_hook: FailureHook | None = None,
) -> list[dict[str, Any]]:
    transitions = transition_store.read_all()
    event_log.materialize(initial_events, transitions, failure_hook)
    writer.materialize(transitions, failure_hook)
    return transitions


def step(
    experiment: ExperimentManifest,
    portfolio: PortfolioSnapshot,
    frame: SimulationFrame,
    generate_target: TargetGenerator,
    *,
    event_log: EventLog,
    writer: RunWriter,
    transition_store: TransitionStore,
    initial_events: Sequence[Mapping[str, Any]],
    benchmarks: Benchmarks,
    slippage_bps: int = 0,
    failure_hook: FailureHook | None = None,
) -> PortfolioSnapshot:
    """Compute, commit, then materialize one portfolio transition."""
    session_key = frame.execution_session.isoformat()
    if session_key in transition_store.completed_sessions():
        return portfolio

    session = frame.execution_session
    if session not in benchmarks:
        raise ValueError(f"missing benchmark equity for session {session}")
    spy_equity, equal_weight_equity = benchmarks[session]

    target, decisions = generate_target(experiment, frame.decision)
    if target.as_of != frame.decision.as_of:
        raise ValueError("target.as_of must equal frame.decision.as_of")

    result = execute_rebalance(
        portfolio,
        target,
        frame.execution,
        filled_at=frame.execution_at,
        slippage_bps=slippage_bps,
    )

    created_at = datetime.now(UTC)
    events = [
        event_record(
            experiment.run_id,
            "target_generated",
            frame.decision.as_of,
            target.model_dump(),
            created_at,
        )
    ]
    order_rows: list[dict[str, Any]] = []
    for order in result.orders:
        events.append(
            event_record(
                experiment.run_id,
                "order_created",
                frame.execution_at,
                order.model_dump(),
                created_at,
            )
        )
        order_rows.append(
            to_jsonable(
                {
                    "as_of": frame.execution_at,
                    "symbol": order.symbol,
                    "side": order.side,
                    "quantity": order.quantity,
                    "status": "created",
                    "reason": None,
                }
            )
        )
    for rejection in result.rejections:
        payload = to_jsonable(
            {
                "as_of": frame.execution_at,
                "symbol": rejection.symbol,
                "side": rejection.side,
                "quantity": rejection.requested_quantity,
                "status": "rejected",
                "reason": rejection.reason,
            }
        )
        events.append(
            event_record(
                experiment.run_id,
                "order_rejected",
                frame.execution_at,
                payload,
                created_at,
            )
        )
        order_rows.append(payload)
    for fill in result.fills:
        events.append(
            event_record(
                experiment.run_id,
                "order_filled",
                frame.execution_at,
                fill.model_dump(),
                created_at,
            )
        )

    marked_row = portfolio_row(result.portfolio)
    events.extend(
        [
            event_record(
                experiment.run_id,
                "portfolio_marked",
                frame.execution.as_of,
                marked_row,
                created_at,
            ),
            event_record(
                experiment.run_id,
                "rebalance_completed",
                frame.execution.as_of,
                {"session": session},
                created_at,
            ),
        ]
    )
    transition = {
        "schema_version": experiment.schema_version,
        "run_id": experiment.run_id,
        "session": session_key,
        "target": to_jsonable(target),
        "decisions": to_jsonable(decisions),
        "orders": order_rows,
        "rejections": [
            {
                "symbol": rejection.symbol,
                "side": rejection.side,
                "requested_quantity": rejection.requested_quantity,
                "reason": rejection.reason,
            }
            for rejection in result.rejections
        ],
        "fills": [fill_row(fill) for fill in result.fills],
        "portfolio": marked_row,
        "references": {
            "spy_equity": str(spy_equity),
            "equal_weight_equity": str(equal_weight_equity),
        },
        "equity": {
            "date": session_key,
            "equity": str(result.portfolio.total_equity),
            "spy_equity": str(spy_equity),
            "equal_weight_equity": str(equal_weight_equity),
        },
        "events": events,
    }

    transition_store.commit(session, transition)
    _materialize(
        event_log,
        writer,
        transition_store,
        initial_events,
        failure_hook,
    )
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
        failure_hook: FailureHook | None = None,
    ) -> None:
        self.experiment = experiment
        self.generate_target = generate_target
        self.benchmarks = benchmarks
        self.slippage_bps = slippage_bps
        self.failure_hook = failure_hook
        self.writer = RunWriter(run_dir)
        self.event_log = EventLog(run_dir / "events.jsonl", experiment.run_id)
        self.transition_store = TransitionStore(run_dir, failure_hook)

        if not self.writer.path("manifest.json").exists():
            self.writer.write_manifest(experiment)
            self.writer.write_philosophy(philosophy_yaml)

        created_as_of = datetime.combine(experiment.start, time(0), tzinfo=UTC)
        self.initial_events = [
            event_record(
                experiment.run_id,
                "portfolio_created",
                created_as_of,
                {"cash": initial_cash, "as_of": created_as_of},
                created_as_of,
            )
        ]
        # Recovery always rebuilds public projections before ledger restoration.
        _materialize(
            self.event_log,
            self.writer,
            self.transition_store,
            self.initial_events,
        )
        self.portfolio = self._restore(self.event_log.read(), initial_cash)

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
            transition_store=self.transition_store,
            initial_events=self.initial_events,
            benchmarks=self.benchmarks,
            slippage_bps=self.slippage_bps,
            failure_hook=self.failure_hook,
        )
        return self.portfolio

    def replay(self, frames: Sequence[SimulationFrame]) -> PortfolioSnapshot:
        """Historical replay mode: loop the same transition over all frames."""
        for frame in sorted(frames, key=lambda item: item.execution.as_of):
            self.step(frame)
        return self.portfolio
