"""Unified replay / forward-paper runner.

Both modes call the single module-level `step` transition (Core Invariant 4):
historical replay loops it over snapshots; forward paper trading calls it once
per newly completed session. Target generation is injected as a callable, so
the runner never imports scoring or allocation.

Immutable per-session journals are authoritative. Public JSONL/CSV projections
are atomically rebuilt from them on startup and exact session retries.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import ValidationError

from retailtrader.domain import (
    ExperimentManifest,
    FillEvent,
    MarketSnapshot,
    OrderIntent,
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
    read_manifest,
)
from retailtrader.storage.events import EventLog, event_record, to_jsonable
from retailtrader.storage.transitions import (
    FailureHook,
    TransitionIntegrityError,
    TransitionStore,
)

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


def _validate_transition_history(
    initial_event: Mapping[str, Any],
    transitions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    events = [dict(initial_event)]
    for transition in transitions:
        events.extend(transition["events"])
    sessions = _validate_event_sessions(events)
    if len(sessions) != len(transitions):
        raise TransitionIntegrityError("journal and completed-session counts differ")
    for transition, session in zip(transitions, sessions, strict=True):
        completed = datetime.fromisoformat(session["execution_as_of"]).date().isoformat()
        if transition["session"] != completed:
            raise TransitionIntegrityError("journal session does not match event history")
        _validate_session_payloads(transition["run_id"], session)
    try:
        replay_events(events)
    except LedgerReplayError as exc:
        raise TransitionIntegrityError(
            f"canonical journal ledger reconstruction failed: {exc}"
        ) from exc
    return events


def _validate_session_payloads(run_id: str, session: Mapping[str, Any]) -> None:
    """Validate canonical event payloads before they become public projections."""
    try:
        target = TargetPortfolio.model_validate(session["target"]["payload"]["target"])
        portfolio = PortfolioSnapshot.model_validate(
            {"run_id": run_id, **session["portfolio"]["payload"]}
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise TransitionIntegrityError(f"invalid target or portfolio payload: {exc}") from exc
    if target.run_id != run_id or portfolio.run_id != run_id:
        raise TransitionIntegrityError("target or portfolio run_id mismatch")
    if target.as_of.isoformat() != session["decision_as_of"]:
        raise TransitionIntegrityError("target payload timestamp mismatch")
    if portfolio.as_of.isoformat() != session["execution_as_of"]:
        raise TransitionIntegrityError("portfolio payload timestamp mismatch")

    created: list[OrderIntent] = []
    for event in session["orders"]:
        payload = event["payload"]
        if event["event_type"] == "order_created":
            try:
                order = OrderIntent.model_validate(payload)
            except ValidationError as exc:
                raise TransitionIntegrityError(f"invalid order_created payload: {exc}") from exc
            if order.run_id != run_id or order.as_of.isoformat() != event["as_of"]:
                raise TransitionIntegrityError("order_created payload identity mismatch")
            created.append(order)
            continue
        required = {"as_of", "symbol", "side", "quantity", "status", "reason"}
        if (
            set(payload) != required
            or payload["as_of"] != event["as_of"]
            or not isinstance(payload["symbol"], str)
            or not payload["symbol"]
            or payload["side"] not in {"buy", "sell"}
            or not isinstance(payload["quantity"], int)
            or isinstance(payload["quantity"], bool)
            or payload["quantity"] <= 0
            or payload["status"] != "rejected"
            or not isinstance(payload["reason"], str)
        ):
            raise TransitionIntegrityError("invalid order_rejected payload")

    fills: list[FillEvent] = []
    for event in session["fills"]:
        try:
            fill = FillEvent.model_validate(event["payload"])
        except ValidationError as exc:
            raise TransitionIntegrityError(f"invalid order_filled payload: {exc}") from exc
        if fill.run_id != run_id or fill.filled_at.isoformat() != event["as_of"]:
            raise TransitionIntegrityError("order_filled payload identity mismatch")
        fills.append(fill)

    order_keys = Counter(
        (order.symbol, order.side, order.quantity, order.as_of.isoformat())
        for order in created
    )
    fill_keys = Counter(
        (fill.symbol, fill.side, fill.quantity, fill.filled_at.isoformat())
        for fill in fills
    )
    if order_keys != fill_keys:
        raise TransitionIntegrityError("created orders and fills do not form a bijection")


def _materialize(
    event_log: EventLog,
    writer: RunWriter,
    initial_event: Mapping[str, Any],
    transitions: Sequence[Mapping[str, Any]],
    equity_header: str,
    failure_hook: FailureHook | None = None,
) -> None:
    _validate_transition_history(initial_event, transitions)
    event_log.materialize(initial_event, transitions, failure_hook)
    writer.materialize(transitions, equity_header, failure_hook)


def step(
    experiment: ExperimentManifest,
    portfolio: PortfolioSnapshot,
    frame: SimulationFrame,
    generate_target: TargetGenerator,
    *,
    event_log: EventLog,
    writer: RunWriter,
    transition_store: TransitionStore,
    initial_event: Mapping[str, Any],
    benchmarks: Benchmarks,
    equity_header: str,
    reference_column: str,
    slippage_bps: int = 0,
    max_turnover: float | None = None,
    failure_hook: FailureHook | None = None,
) -> PortfolioSnapshot:
    """Compute, atomically commit, then materialize one portfolio transition."""
    with transition_store.locked():
        transition_store.ensure_durable()
        transitions = transition_store.read_all()
        _validate_transition_history(initial_event, transitions)
        session = frame.execution_session
        session_key = session.isoformat()
        if transitions:
            portfolio = portfolio_from_row(experiment.run_id, transitions[-1]["events"][-2]["payload"])
            completed = {transition["session"] for transition in transitions}
            if session_key in completed:
                _materialize(
                    event_log,
                    writer,
                    initial_event,
                    transitions,
                    equity_header,
                    failure_hook,
                )
                return portfolio
            if session_key <= transitions[-1]["session"]:
                raise TransitionIntegrityError(
                    f"execution session {session_key} is not later than latest committed session"
                )
        if session not in benchmarks:
            raise ValueError(f"missing benchmark equity for session {session}")
        reference_equity, equal_weight_equity = benchmarks[session]

        target, decisions = generate_target(experiment, frame.decision)
        if target.as_of != frame.decision.as_of:
            raise ValueError("target.as_of must equal frame.decision.as_of")
        result = execute_rebalance(
            portfolio,
            target,
            frame.execution,
            filled_at=frame.execution_at,
            slippage_bps=slippage_bps,
            max_turnover=max_turnover,
        )

        created_at = datetime.now(UTC)
        events = [
            event_record(
                experiment.run_id,
                "target_generated",
                frame.decision.as_of,
                {"target": target.model_dump(), "decisions": decisions},
                created_at,
            )
        ]
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
        for rejection in result.rejections:
            events.append(
                event_record(
                    experiment.run_id,
                    "order_rejected",
                    frame.execution_at,
                    {
                        "as_of": frame.execution_at,
                        "symbol": rejection.symbol,
                        "side": rejection.side,
                        "quantity": rejection.requested_quantity,
                        "status": "rejected",
                        "reason": rejection.reason,
                    },
                    created_at,
                )
            )
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
        events.extend(
            [
                event_record(
                    experiment.run_id,
                    "portfolio_marked",
                    frame.execution.as_of,
                    portfolio_row(result.portfolio),
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
            "reference_column": reference_column,
            "reference_equity": str(reference_equity),
            "equal_weight_equity": str(equal_weight_equity),
            "events": events,
        }
        transition_store.commit(session, transition)
        transitions = transition_store.read_all()
        _materialize(
            event_log,
            writer,
            initial_event,
            transitions,
            equity_header,
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
        max_turnover: float | None = None,
        data_provenance: Mapping[str, Any] | None = None,
        reference_column: str = "synthetic_mega_cap_proxy_equity",
        failure_hook: FailureHook | None = None,
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
        self.reference_column = reference_column
        self.failure_hook = failure_hook
        self._step_lock = Lock()
        self.writer = RunWriter(run_dir)
        created_as_of = datetime.combine(experiment.start, time(0), tzinfo=UTC)
        self.transition_store = TransitionStore(
            run_dir,
            failure_hook,
            run_id=experiment.run_id,
            schema_version=experiment.schema_version,
            reference_column=reference_column,
        )

        with self.transition_store.locked():
            existing = any(run_dir.iterdir())
            try:
                if existing:
                    self.experiment = self._validate_identity(
                        run_dir,
                        experiment,
                        philosophy_yaml,
                        data_provenance,
                    )
                    state = self.transition_store.read_state(
                        created_as_of=created_as_of,
                        initial_cash=self.experiment.initial_cash,
                        slippage_bps=self.experiment.slippage_bps,
                        max_turnover=max_turnover,
                    )
                else:
                    self.experiment = experiment
                    self.writer.write_manifest(experiment)
                    self.writer.write_philosophy(philosophy_yaml)
                    if data_provenance is not None:
                        self.writer.write_data_provenance(dict(data_provenance))
                    state = self.transition_store.initialize_state(
                        created_as_of=created_as_of,
                        initial_cash=experiment.initial_cash,
                        slippage_bps=experiment.slippage_bps,
                        max_turnover=max_turnover,
                    )
                self.event_log = EventLog(run_dir / "events.jsonl", self.experiment.run_id)
                self.initial_event = event_record(
                    self.experiment.run_id,
                    "portfolio_created",
                    datetime.fromisoformat(state["created_as_of"]),
                    {
                        "cash": state["initial_cash"],
                        "as_of": state["created_as_of"],
                        "slippage_bps": state["slippage_bps"],
                        "max_turnover": state["max_turnover"],
                    },
                    datetime.fromisoformat(state["created_as_of"]),
                )
                self.transition_store.ensure_durable()
                transitions = self.transition_store.read_all()
                _materialize(
                    self.event_log,
                    self.writer,
                    self.initial_event,
                    transitions,
                    self.equity_header,
                )
                self.portfolio = self._restore_transitions(transitions, state)
            except TransitionIntegrityError as exc:
                raise ResumeMismatchError(str(exc)) from exc

    @staticmethod
    def _validate_identity(
        run_dir: Path,
        incoming: ExperimentManifest,
        philosophy_yaml: str,
        data_provenance: Mapping[str, Any] | None,
    ) -> ExperimentManifest:
        required = ["manifest.json", "philosophy.yaml", "run-state.json"]
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
            if field != "created_at" and getattr(persisted, field) != getattr(incoming, field):
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
                persisted_provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ResumeMismatchError(f"data-provenance.json: {exc}") from exc
            if persisted_provenance != to_jsonable(dict(data_provenance)):
                raise ResumeMismatchError("data-provenance.json mismatch")
        return persisted

    def _restore_transitions(
        self,
        transitions: Sequence[Mapping[str, Any]],
        state: Mapping[str, Any],
    ) -> PortfolioSnapshot:
        if transitions:
            return portfolio_from_row(
                self.experiment.run_id,
                transitions[-1]["events"][-2]["payload"],
            )
        return initial_portfolio(
            self.experiment,
            Decimal(state["initial_cash"]),
            datetime.fromisoformat(state["created_as_of"]),
        )

    def step(self, frame: SimulationFrame) -> PortfolioSnapshot:
        """Forward paper mode: process one decision/execution frame."""
        with self._step_lock:
            self.portfolio = step(
                self.experiment,
                self.portfolio,
                frame,
                self.generate_target,
                event_log=self.event_log,
                writer=self.writer,
                transition_store=self.transition_store,
                initial_event=self.initial_event,
                benchmarks=self.benchmarks,
                equity_header=self.equity_header,
                reference_column=self.reference_column,
                slippage_bps=self.experiment.slippage_bps,
                max_turnover=self.max_turnover,
                failure_hook=self.failure_hook,
            )
            return self.portfolio

    def replay(self, frames: Sequence[SimulationFrame]) -> PortfolioSnapshot:
        """Historical replay mode: loop the same transition over all frames."""
        ordered = sorted(frames, key=lambda item: item.execution.as_of)
        completed = [
            date.fromisoformat(transition["session"])
            for transition in self.transition_store.read_all()
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
