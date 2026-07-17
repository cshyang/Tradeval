"""Unified replay / forward-paper runner.

Both modes call the single module-level `step` transition (Core Invariant 4):
historical replay loops it over snapshots; forward paper trading calls it once
per newly completed session. Target generation is injected as a callable, so
the runner never imports scoring or allocation.

Idempotency: a session whose `rebalance_completed` event already exists in the
event log is skipped without writing anything.
"""

from __future__ import annotations

import json
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
from retailtrader.simulation.ledger import LedgerReplayError, replay_events
from retailtrader.storage.artifacts import (
    EQUITY_HEADER,
    SPY_EQUITY_HEADER,
    RunWriter,
    portfolio_row,
    read_jsonl,
    read_manifest,
)
from retailtrader.storage.events import EVENT_TYPES, EventLog, to_jsonable

TargetGenerator = Callable[
    [ExperimentManifest, MarketSnapshot],
    tuple[TargetPortfolio, list[dict[str, Any]]],
]
Benchmarks = Mapping[date, tuple[Decimal, Decimal]]


class ResumeMismatchError(ValueError):
    """Persisted run identity does not match the requested experiment."""


def _validate_event_sessions(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if events[0]["event_type"] != "portfolio_created":
        raise ResumeMismatchError("events.jsonl: portfolio_created must be first")

    sessions: list[dict[str, Any]] = []
    index = 1
    while index < len(events):
        target = events[index]
        if target["event_type"] != "target_generated":
            raise ResumeMismatchError(
                f"events.jsonl: invalid session start at event {index}"
            )
        if (
            set(target["payload"]) != {"target", "decisions"}
            or not isinstance(target["payload"]["target"], dict)
            or not isinstance(target["payload"]["decisions"], list)
            or not all(
                isinstance(decision, dict)
                for decision in target["payload"]["decisions"]
            )
        ):
            raise ResumeMismatchError(
                f"events.jsonl: invalid target payload at event {index}"
            )
        decision_as_of = datetime.fromisoformat(target["as_of"])
        if target["payload"]["target"].get("as_of") != target["as_of"]:
            raise ResumeMismatchError("events.jsonl: target decision timestamp mismatch")
        index += 1
        orders: list[dict[str, Any]] = []
        fills: list[dict[str, Any]] = []
        marked: dict[str, Any] | None = None
        execution_at: datetime | None = None
        phase = "orders"

        while index < len(events):
            event = events[index]
            event_type = event["event_type"]
            event_as_of = datetime.fromisoformat(event["as_of"])
            if event_type in {"order_created", "order_rejected"} and phase == "orders":
                if event_as_of <= decision_as_of:
                    raise ResumeMismatchError("events.jsonl: order precedes decision")
                execution_at = execution_at or event_as_of
                if event_as_of != execution_at:
                    raise ResumeMismatchError("events.jsonl: inconsistent execution times")
                orders.append(event)
            elif event_type == "order_filled" and phase in {"orders", "fills"}:
                phase = "fills"
                if event_as_of <= decision_as_of:
                    raise ResumeMismatchError("events.jsonl: fill precedes decision")
                execution_at = execution_at or event_as_of
                if event_as_of != execution_at:
                    raise ResumeMismatchError("events.jsonl: inconsistent execution times")
                fills.append(event)
            elif event_type == "portfolio_marked" and phase in {"orders", "fills"}:
                if event_as_of <= (execution_at or decision_as_of):
                    raise ResumeMismatchError("events.jsonl: mark precedes execution")
                phase = "marked"
                marked = event
            elif event_type == "rebalance_completed" and phase == "marked":
                if marked is None or event["as_of"] != marked["as_of"]:
                    raise ResumeMismatchError("events.jsonl: completion timestamp mismatch")
                expected_session = datetime.fromisoformat(marked["as_of"]).date().isoformat()
                if event["payload"].get("session") != expected_session:
                    raise ResumeMismatchError(
                        f"events.jsonl: completed session payload mismatch at {marked['as_of']}"
                    )
                sessions.append(
                    {
                        "decision_as_of": target["as_of"],
                        "execution_as_of": marked["as_of"],
                        "target": target,
                        "orders": orders,
                        "fills": fills,
                        "portfolio": marked,
                    }
                )
                index += 1
                break
            else:
                raise ResumeMismatchError(
                    f"events.jsonl: invalid event sequence after {target['as_of']}"
                )
            index += 1
        else:
            raise ResumeMismatchError(
                f"events.jsonl: incomplete session after {target['as_of']}"
            )

    completed = [
        datetime.fromisoformat(session["execution_as_of"]) for session in sessions
    ]
    if completed != sorted(set(completed)):
        raise ResumeMismatchError("completed sessions are not chronological and unique")
    return sessions


def _read_materialized_jsonl(run_dir: Path, name: str) -> list[dict[str, Any]]:
    path = run_dir / name
    if not path.is_file():
        raise ResumeMismatchError(f"missing materialized artifact: {name}")
    try:
        rows = read_jsonl(path)
    except Exception as exc:
        raise ResumeMismatchError(f"{name}: malformed JSONL: {exc}") from exc
    if not all(isinstance(row, dict) for row in rows):
        raise ResumeMismatchError(f"{name}: rows must be objects")
    return rows


def _validate_materialized_artifacts(
    run_dir: Path,
    sessions: Sequence[dict[str, Any]],
    benchmarks: Benchmarks,
    equity_header: str,
) -> None:
    decisions = _read_materialized_jsonl(run_dir, "decisions.jsonl")
    expected_decisions = [
        decision
        for session in sessions
        for decision in session["target"]["payload"].get("decisions", [])
    ]
    if decisions != expected_decisions:
        raise ResumeMismatchError("decisions.jsonl: rows do not match target events")

    orders = _read_materialized_jsonl(run_dir, "orders.jsonl")
    expected_orders = []
    for session in sessions:
        for event in session["orders"]:
            payload = event["payload"]
            if event["event_type"] == "order_rejected":
                expected_orders.append(payload)
            else:
                expected_orders.append(
                    {
                        "as_of": payload["as_of"],
                        "symbol": payload["symbol"],
                        "side": payload["side"],
                        "quantity": payload["quantity"],
                        "status": "created",
                        "reason": None,
                    }
                )
    if orders != expected_orders:
        raise ResumeMismatchError("orders.jsonl: rows do not match order events")

    fills = _read_materialized_jsonl(run_dir, "fills.jsonl")
    expected_fills = [
        {
            "symbol": event["payload"]["symbol"],
            "side": event["payload"]["side"],
            "quantity": event["payload"]["quantity"],
            "fill_price": event["payload"]["fill_price"],
            "filled_at": event["payload"]["filled_at"],
        }
        for session in sessions
        for event in session["fills"]
    ]
    if fills != expected_fills:
        raise ResumeMismatchError("fills.jsonl: rows do not match fill events")

    portfolios = _read_materialized_jsonl(run_dir, "portfolio.jsonl")
    expected_portfolios = [session["portfolio"]["payload"] for session in sessions]
    if portfolios != expected_portfolios:
        raise ResumeMismatchError("portfolio.jsonl: rows do not match portfolio events")

    equity_path = run_dir / "equity.csv"
    if not equity_path.is_file():
        raise ResumeMismatchError("missing materialized artifact: equity.csv")
    try:
        lines = equity_path.read_text(encoding="utf-8").splitlines()
        if not lines or lines[0] != equity_header:
            raise ValueError("unexpected header")
        rows = [line.split(",") for line in lines[1:]]
        if any(len(row) != 4 for row in rows):
            raise ValueError("expected four columns")
    except Exception as exc:
        raise ResumeMismatchError(f"equity.csv: malformed CSV: {exc}") from exc

    expected_equity = []
    for session in sessions:
        session_date = datetime.fromisoformat(session["execution_as_of"]).date()
        if session_date not in benchmarks:
            raise ResumeMismatchError(f"equity.csv: missing benchmark for {session_date}")
        proxy_equity, equal_weight_equity = benchmarks[session_date]
        expected_equity.append(
            [
                session_date.isoformat(),
                f'{Decimal(session["portfolio"]["payload"]["total_equity"]):.2f}',
                f"{proxy_equity:.2f}",
                f"{equal_weight_equity:.2f}",
            ]
        )
    if rows != expected_equity:
        raise ResumeMismatchError("equity.csv: rows do not match completed sessions")


def remaining_session_suffix(
    completed_sessions: Sequence[date], scheduled_sessions: Sequence[date]
) -> tuple[date, ...]:
    """Validate completed sessions as a prefix and return the unprocessed suffix."""
    completed = tuple(completed_sessions)
    scheduled = tuple(scheduled_sessions)
    if scheduled != tuple(sorted(set(scheduled))):
        raise ResumeMismatchError("scheduled sessions must be chronological and unique")
    if completed != scheduled[: len(completed)]:
        raise ResumeMismatchError("completed sessions are not a chronological prefix")
    return scheduled[len(completed) :]


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
    max_turnover: float | None = None,
) -> PortfolioSnapshot:
    """Single prior-close/next-open transition for replay and paper stepping."""
    session = frame.execution_session
    if session.isoformat() in event_log.completed_sessions():
        return portfolio
    if session not in benchmarks:
        raise ValueError(f"missing benchmark equity for session {session}")
    proxy_equity, equal_weight_equity = benchmarks[session]

    target, decisions = generate_target(experiment, frame.decision)
    if target.as_of != frame.decision.as_of:
        raise ValueError("target.as_of must equal frame.decision.as_of")
    event_log.append(
        "target_generated",
        frame.decision.as_of,
        {"target": target.model_dump(), "decisions": decisions},
    )
    for record in decisions:
        writer.append_decision(record)

    result = execute_rebalance(
        portfolio,
        target,
        frame.execution,
        filled_at=frame.execution_at,
        slippage_bps=slippage_bps,
        max_turnover=max_turnover,
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

    event_log.append(
        "portfolio_marked", frame.execution.as_of, portfolio_row(result.portfolio)
    )
    writer.append_portfolio(result.portfolio)
    writer.append_equity_row(
        session, result.portfolio.total_equity, proxy_equity, equal_weight_equity
    )
    event_log.append(
        "rebalance_completed", frame.execution.as_of, {"session": session}
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
        max_turnover: float | None = None,
        data_provenance: Mapping[str, Any] | None = None,
        reference_column: str = "synthetic_mega_cap_proxy_equity",
    ) -> None:
        headers = {
            "synthetic_mega_cap_proxy_equity": EQUITY_HEADER,
            "spy_equity": SPY_EQUITY_HEADER,
        }
        if reference_column not in headers:
            raise ValueError(f"unsupported reference column: {reference_column}")
        self.equity_header = headers[reference_column]
        if max_turnover is not None and not 0 <= max_turnover <= 1:
            raise ValueError("max_turnover must be within [0, 1]")
        self.generate_target = generate_target
        self.benchmarks = benchmarks
        self.max_turnover = max_turnover
        existing = run_dir.exists() and any(run_dir.iterdir())

        if existing:
            persisted, events = self._validate_resume(
                run_dir,
                experiment,
                philosophy_yaml,
                max_turnover,
                benchmarks,
                data_provenance,
                self.equity_header,
            )
            self.experiment = persisted
            self.writer = RunWriter(run_dir)
            self.event_log = EventLog(run_dir / "events.jsonl", persisted.run_id)
            try:
                self.portfolio = self._restore(events)
            except Exception as exc:
                raise ResumeMismatchError(f"events.jsonl: invalid portfolio state: {exc}") from exc
        else:
            self.experiment = experiment
            self.writer = RunWriter(run_dir)
            self.event_log = EventLog(run_dir / "events.jsonl", experiment.run_id)
            self.writer.write_manifest(experiment)
            self.writer.write_philosophy(philosophy_yaml)
            if data_provenance is not None:
                self.writer.write_data_provenance(data_provenance)
            self.writer.initialize_materialized(self.equity_header)
            created_as_of = datetime.combine(experiment.start, time(0), tzinfo=UTC)
            self.portfolio = initial_portfolio(
                experiment, experiment.initial_cash, created_as_of
            )
            self.event_log.append(
                "portfolio_created",
                created_as_of,
                {
                    "cash": experiment.initial_cash,
                    "as_of": created_as_of,
                    "slippage_bps": experiment.slippage_bps,
                    "max_turnover": max_turnover,
                },
            )

    @staticmethod
    def _validate_resume(
        run_dir: Path,
        incoming: ExperimentManifest,
        philosophy_yaml: str,
        max_turnover: float | None,
        benchmarks: Benchmarks,
        data_provenance: Mapping[str, Any] | None,
        equity_header: str,
    ) -> tuple[ExperimentManifest, list[dict[str, Any]]]:
        required = ["manifest.json", "philosophy.yaml", "events.jsonl"]
        if data_provenance is not None:
            required.append("data-provenance.json")
        for name in required:
            if not (run_dir / name).is_file():
                raise ResumeMismatchError(f"missing identity artifact: {name}")

        try:
            persisted = read_manifest(run_dir / "manifest.json")
        except Exception as exc:
            raise ResumeMismatchError(f"manifest.json: {exc}") from exc

        for field in ExperimentManifest.model_fields:
            if field == "created_at":
                continue
            if getattr(persisted, field) != getattr(incoming, field):
                raise ResumeMismatchError(f"manifest {field} mismatch")

        try:
            persisted_yaml = (run_dir / "philosophy.yaml").read_text(encoding="utf-8")
        except Exception as exc:
            raise ResumeMismatchError(f"philosophy.yaml: {exc}") from exc
        if persisted_yaml != philosophy_yaml:
            raise ResumeMismatchError("philosophy.yaml mismatch")

        provenance_path = run_dir / "data-provenance.json"
        if provenance_path.exists():
            if data_provenance is None:
                raise ResumeMismatchError("unexpected data-provenance.json")
            try:
                persisted_provenance = json.loads(
                    provenance_path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise ResumeMismatchError(f"data-provenance.json: {exc}") from exc
            if persisted_provenance != to_jsonable(dict(data_provenance)):
                raise ResumeMismatchError("data-provenance.json mismatch")

        try:
            events = EventLog(run_dir / "events.jsonl", persisted.run_id).read()
        except Exception as exc:
            raise ResumeMismatchError(f"events.jsonl: {exc}") from exc
        if not events:
            raise ResumeMismatchError("events.jsonl: event log is empty")

        required_keys = {
            "schema_version",
            "run_id",
            "event_type",
            "as_of",
            "created_at",
            "payload",
        }
        for index, event in enumerate(events):
            if not isinstance(event, dict) or set(event) != required_keys:
                raise ResumeMismatchError(f"events.jsonl event {index}: invalid envelope")
            if event["run_id"] != persisted.run_id:
                raise ResumeMismatchError(f"events.jsonl event {index}: run_id mismatch")
            if event["schema_version"] != persisted.schema_version:
                raise ResumeMismatchError(
                    f"events.jsonl event {index}: schema_version mismatch"
                )
            if event["event_type"] not in EVENT_TYPES:
                raise ResumeMismatchError(f"events.jsonl event {index}: unknown event_type")
            try:
                as_of = datetime.fromisoformat(event["as_of"])
                created_at = datetime.fromisoformat(event["created_at"])
            except (TypeError, ValueError) as exc:
                raise ResumeMismatchError(
                    f"events.jsonl event {index}: invalid timestamp"
                ) from exc
            if as_of.tzinfo is None or created_at.tzinfo is None:
                raise ResumeMismatchError(
                    f"events.jsonl event {index}: timestamp must be timezone-aware"
                )
            if not isinstance(event["payload"], dict):
                raise ResumeMismatchError(f"events.jsonl event {index}: invalid payload")

        created = [event for event in events if event["event_type"] == "portfolio_created"]
        if len(created) != 1:
            raise ResumeMismatchError("events.jsonl: expected one portfolio_created event")
        try:
            created_cash = Decimal(created[0]["payload"]["cash"])
            datetime.fromisoformat(created[0]["payload"]["as_of"])
            created_slippage = int(created[0]["payload"]["slippage_bps"])
            created_max_turnover = created[0]["payload"]["max_turnover"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ResumeMismatchError("events.jsonl: invalid portfolio_created payload") from exc
        if created_cash != persisted.initial_cash:
            raise ResumeMismatchError("events.jsonl initial_cash mismatch")
        if created_slippage != persisted.slippage_bps:
            raise ResumeMismatchError("events.jsonl slippage_bps mismatch")
        if created_max_turnover != max_turnover:
            raise ResumeMismatchError("events.jsonl max_turnover mismatch")

        sessions = _validate_event_sessions(events)
        try:
            replay_events(events)
        except LedgerReplayError as exc:
            raise ResumeMismatchError(
                f"events.jsonl: ledger reconstruction failed: {exc}"
            ) from exc
        try:
            _validate_materialized_artifacts(
                run_dir, sessions, benchmarks, equity_header
            )
        except ResumeMismatchError:
            raise
        except Exception as exc:
            raise ResumeMismatchError(
                f"events.jsonl: invalid materialization payload: {exc}"
            ) from exc
        return persisted, events

    def _restore(self, events: Sequence[Mapping[str, Any]]) -> PortfolioSnapshot:
        marked = [event for event in events if event["event_type"] == "portfolio_marked"]
        if marked:
            return portfolio_from_row(self.experiment.run_id, marked[-1]["payload"])
        created = next(event for event in events if event["event_type"] == "portfolio_created")
        return initial_portfolio(
            self.experiment,
            Decimal(created["payload"]["cash"]),
            datetime.fromisoformat(created["payload"]["as_of"]),
        )

    def step(self, frame: SimulationFrame) -> PortfolioSnapshot:
        """Forward paper mode: process one decision/execution frame."""
        self.portfolio = step(
            self.experiment,
            self.portfolio,
            frame,
            self.generate_target,
            event_log=self.event_log,
            writer=self.writer,
            benchmarks=self.benchmarks,
            slippage_bps=self.experiment.slippage_bps,
            max_turnover=self.max_turnover,
        )
        return self.portfolio

    def replay(self, frames: Sequence[SimulationFrame]) -> PortfolioSnapshot:
        """Historical replay mode: loop the same transition over all frames."""
        ordered = sorted(frames, key=lambda item: item.execution.as_of)
        completed = [
            date.fromisoformat(event["payload"]["session"])
            for event in self.event_log.read()
            if event["event_type"] == "rebalance_completed"
        ]
        remaining = set(
            remaining_session_suffix(
                completed, [item.execution_session for item in ordered]
            )
        )
        for frame in ordered:
            if frame.execution_session not in remaining:
                continue
            self.step(frame)
        return self.portfolio
