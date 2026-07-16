"""Crash-safe, immutable journals for completed simulation transitions."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from retailtrader.domain import (
    FillEvent,
    OrderIntent,
    PortfolioSnapshot,
    TargetPortfolio,
)
from retailtrader.storage.events import EVENT_TYPES

FailureHook = Callable[[str], None]


class TransitionIntegrityError(RuntimeError):
    """A transition journal is conflicting, malformed, or belongs to another run."""


def _canonical_bytes(transition: Mapping[str, Any]) -> bytes:
    return (json.dumps(transition, indent=2, sort_keys=True) + "\n").encode()


def _session_key(session: date | str) -> str:
    value = session.isoformat() if isinstance(session, date) else session
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid execution session: {value}") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"execution session must be an ISO date: {value}")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TransitionIntegrityError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise TransitionIntegrityError(f"{label} must be a list")
    return value


def _require(value: Mapping[str, Any], fields: set[str], label: str) -> None:
    missing = fields - value.keys()
    if missing:
        raise TransitionIntegrityError(
            f"{label} is missing required fields: {', '.join(sorted(missing))}"
        )


def _datetime(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise TransitionIntegrityError(f"{label} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TransitionIntegrityError(f"{label} must be an ISO datetime") from exc
    if parsed.tzinfo is None:
        raise TransitionIntegrityError(f"{label} must be timezone-aware")
    return parsed


def _decimal(value: Any, label: str) -> Decimal:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise TransitionIntegrityError(f"{label} must be numeric")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise TransitionIntegrityError(f"{label} must be numeric") from exc
    if not parsed.is_finite():
        raise TransitionIntegrityError(f"{label} must be finite")
    return parsed


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise TransitionIntegrityError(f"{label} must be a positive integer")
    return value


class TransitionStore:
    """Own one atomically committed source-of-truth journal per session.

    ``locked`` is deliberately not re-entrant. The runner owns the run-level
    lock around startup and complete transitions; low-level read/commit methods
    assume their caller already owns it when public projections are involved.
    """

    def __init__(
        self,
        run_dir: Path,
        failure_hook: FailureHook | None = None,
        *,
        run_id: str | None = None,
        schema_version: int | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.directory = run_dir / "transitions"
        self.metadata_path = run_dir / "initial-state.json"
        self.lock_path = run_dir / ".transitions.lock"
        self.failure_hook = failure_hook
        self.run_id = run_id
        self.schema_version = schema_version

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Exclusively serialize one run across threads and processes."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _fail(self, point: str) -> None:
        if self.failure_hook is not None:
            self.failure_hook(point)

    def path(self, session: date | str) -> Path:
        return self.directory / f"{_session_key(session)}.json"

    def _ensure_directory(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        # Persist the transitions/ entry itself, retrying this fsync on every
        # commit in case a prior first-creation attempt failed before durability.
        descriptor = os.open(self.run_dir, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _fsync_run_directory(self) -> None:
        descriptor = os.open(self.run_dir, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _fsync_directory(self) -> None:
        self._fail("before_parent_fsync")
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fail("after_parent_fsync")

    def initialize_metadata(
        self,
        *,
        run_id: str,
        schema_version: int,
        initial_cash: Decimal,
        created_as_of: datetime,
    ) -> dict[str, Any]:
        """Create or validate immutable initial state while the caller owns the lock."""
        expected = {
            "schema_version": schema_version,
            "run_id": run_id,
            "initial_cash": str(initial_cash),
            "created_as_of": created_as_of.isoformat(),
        }
        content = _canonical_bytes(expected)
        if self.metadata_path.exists():
            try:
                existing = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TransitionIntegrityError(
                    "cannot read immutable initial-state metadata"
                ) from exc
            if existing != expected:
                raise TransitionIntegrityError("immutable initial-state metadata mismatch")
            self._fsync_run_directory()
            return expected

        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.run_dir,
                prefix=".initial-state.",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, self.metadata_path)
            except FileExistsError:
                if self.metadata_path.read_bytes() != content:
                    raise TransitionIntegrityError(
                        "immutable initial-state metadata mismatch"
                    ) from None
            temporary.unlink()
            temporary = None
            self._fsync_run_directory()
            return expected
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def commit(self, session: date | str, transition: Mapping[str, Any]) -> None:
        """Durably publish a journal, accepting only exact idempotent retries."""
        session_key = _session_key(session)
        if transition.get("session") != session_key:
            raise ValueError("transition session does not match journal session")
        target = self.path(session_key)
        self._validate(transition, target)
        content = _canonical_bytes(transition)
        self._ensure_directory()
        if target.exists():
            if target.read_bytes() == content:
                self._fsync_directory()
                return
            raise TransitionIntegrityError(
                f"conflicting transition journal for session {session_key}"
            )

        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.directory, prefix=f".{session_key}.", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())

            self._fail("before_journal_replace")
            try:
                # Both paths are in the journal directory, so creating the hard
                # link atomically publishes the complete, fsynced file without
                # ever overwriting another writer's journal.
                os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() == content:
                    temporary.unlink()
                    temporary = None
                    self._fsync_directory()
                    return
                raise TransitionIntegrityError(
                    f"conflicting transition journal for session {session_key}"
                ) from None
            temporary.unlink()
            temporary = None
            self._fail("after_journal_replace")
            self._fsync_directory()
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _validate_decisions(self, value: Any) -> None:
        for index, raw in enumerate(_list(value, "decisions")):
            label = f"decisions[{index}]"
            decision = _mapping(raw, label)
            _require(decision, {"as_of", "selected", "rejected"}, label)
            _datetime(decision["as_of"], f"{label}.as_of")
            for item_index, raw_item in enumerate(_list(decision["selected"], f"{label}.selected")):
                item_label = f"{label}.selected[{item_index}]"
                item = _mapping(raw_item, item_label)
                _require(item, {"symbol", "weight", "score", "factors"}, item_label)
                if not isinstance(item["symbol"], str):
                    raise TransitionIntegrityError(f"{item_label}.symbol must be a string")
                _decimal(item["weight"], f"{item_label}.weight")
                _decimal(item["score"], f"{item_label}.score")
                for factor_index, raw_factor in enumerate(
                    _list(item["factors"], f"{item_label}.factors")
                ):
                    factor_label = f"{item_label}.factors[{factor_index}]"
                    factor = _mapping(raw_factor, factor_label)
                    _require(factor, {"name", "value", "contribution"}, factor_label)
                    if not isinstance(factor["name"], str):
                        raise TransitionIntegrityError(f"{factor_label}.name must be a string")
                    if factor["value"] is not None:
                        _decimal(factor["value"], f"{factor_label}.value")
                    _decimal(factor["contribution"], f"{factor_label}.contribution")
            for item_index, raw_item in enumerate(_list(decision["rejected"], f"{label}.rejected")):
                item_label = f"{label}.rejected[{item_index}]"
                item = _mapping(raw_item, item_label)
                _require(item, {"symbol", "reason", "score"}, item_label)
                if not isinstance(item["symbol"], str) or not isinstance(item["reason"], str):
                    raise TransitionIntegrityError(f"{item_label} has invalid symbol or reason")
                if item["score"] is not None:
                    _decimal(item["score"], f"{item_label}.score")

    def _validate_orders(self, value: Any, session: str) -> None:
        required = {"as_of", "symbol", "side", "quantity", "status", "reason"}
        for index, raw in enumerate(_list(value, "orders")):
            order = _mapping(raw, f"orders[{index}]")
            _require(order, required, f"orders[{index}]")
            if _datetime(order["as_of"], f"orders[{index}].as_of").date().isoformat() != session:
                raise TransitionIntegrityError(f"orders[{index}].as_of does not match session")
            if order["side"] not in {"buy", "sell"} or order["status"] not in {
                "created",
                "rejected",
            }:
                raise TransitionIntegrityError(f"orders[{index}] has invalid side or status")
            if not isinstance(order["symbol"], str):
                raise TransitionIntegrityError(f"orders[{index}].symbol must be a string")
            _positive_int(order["quantity"], f"orders[{index}].quantity")
            if order["reason"] is not None and not isinstance(order["reason"], str):
                raise TransitionIntegrityError(f"orders[{index}].reason must be null or a string")

    def _validate_rejections(self, value: Any) -> None:
        required = {"symbol", "side", "requested_quantity", "reason"}
        for index, raw in enumerate(_list(value, "rejections")):
            rejection = _mapping(raw, f"rejections[{index}]")
            _require(rejection, required, f"rejections[{index}]")
            if not isinstance(rejection["symbol"], str) or rejection["side"] not in {
                "buy",
                "sell",
            }:
                raise TransitionIntegrityError(f"rejections[{index}] has invalid symbol or side")
            _positive_int(
                rejection["requested_quantity"], f"rejections[{index}].requested_quantity"
            )
            if not isinstance(rejection["reason"], str):
                raise TransitionIntegrityError(f"rejections[{index}].reason must be a string")

    def _validate_events(
        self,
        value: Any,
        session: str,
        transition: Mapping[str, Any],
    ) -> None:
        events = _list(value, "events")
        allowed = EVENT_TYPES - {"portfolio_created"}
        completions = []
        event_payloads: dict[str, list[dict[str, Any]]] = {}
        for index, raw in enumerate(events):
            event = _mapping(raw, f"events[{index}]")
            _require(
                event,
                {"schema_version", "run_id", "event_type", "as_of", "created_at", "payload"},
                f"events[{index}]",
            )
            if event["schema_version"] != transition["schema_version"] or (
                self.schema_version is not None and event["schema_version"] != self.schema_version
            ):
                raise TransitionIntegrityError(f"events[{index}] schema_version mismatch")
            if event["run_id"] != transition["run_id"] or (
                self.run_id is not None and event["run_id"] != self.run_id
            ):
                raise TransitionIntegrityError(f"events[{index}] run_id mismatch")
            if event["event_type"] not in allowed:
                raise TransitionIntegrityError(f"events[{index}] has invalid event_type")
            _datetime(event["as_of"], f"events[{index}].as_of")
            _datetime(event["created_at"], f"events[{index}].created_at")
            payload = _mapping(event["payload"], f"events[{index}].payload")
            event_type = event["event_type"]
            event_payloads.setdefault(event_type, []).append(dict(payload))
            try:
                if event_type == "target_generated":
                    target = TargetPortfolio.model_validate(payload)
                    if target.run_id != transition["run_id"] or payload != transition["target"]:
                        raise TransitionIntegrityError(
                            "target_generated payload does not match transition target"
                        )
                elif event_type == "order_created":
                    order = OrderIntent.model_validate(payload)
                    if order.run_id != transition["run_id"]:
                        raise TransitionIntegrityError("order_created payload run_id mismatch")
                elif event_type == "order_filled":
                    fill = FillEvent.model_validate(payload)
                    if fill.run_id != transition["run_id"]:
                        raise TransitionIntegrityError("order_filled payload run_id mismatch")
                elif event_type == "portfolio_marked":
                    marked = PortfolioSnapshot.model_validate(
                        {"run_id": transition["run_id"], **payload}
                    )
                    if marked.run_id != transition["run_id"] or payload != transition["portfolio"]:
                        raise TransitionIntegrityError(
                            "portfolio_marked payload does not match transition portfolio"
                        )
                elif event_type == "order_rejected":
                    self._validate_orders([payload], session)
            except ValidationError as exc:
                raise TransitionIntegrityError(f"invalid events[{index}] payload: {exc}") from exc
            if event_type not in {"target_generated"} and (
                _datetime(event["as_of"], f"events[{index}].as_of").date().isoformat() != session
            ):
                raise TransitionIntegrityError(f"events[{index}].as_of does not match session")
            if event_type == "rebalance_completed":
                completions.append(event)
                if payload != {"session": session}:
                    raise TransitionIntegrityError("rebalance_completed session mismatch")
        for required_type in ("target_generated", "portfolio_marked", "rebalance_completed"):
            if len(event_payloads.get(required_type, [])) != 1:
                raise TransitionIntegrityError(
                    f"journal must contain exactly one {required_type} event"
                )

        created_orders = [order for order in transition["orders"] if order["status"] == "created"]
        created_event_rows = [
            {
                "as_of": payload["as_of"],
                "symbol": payload["symbol"],
                "side": payload["side"],
                "quantity": payload["quantity"],
                "status": "created",
                "reason": None,
            }
            for payload in event_payloads.get("order_created", [])
        ]
        if created_event_rows != created_orders:
            raise TransitionIntegrityError("order_created events do not match created order rows")

        rejected_orders = [order for order in transition["orders"] if order["status"] == "rejected"]
        rejected_events = event_payloads.get("order_rejected", [])
        if rejected_events != rejected_orders:
            raise TransitionIntegrityError("order_rejected events do not match rejected order rows")
        rejection_rows = [
            {
                "symbol": order["symbol"],
                "side": order["side"],
                "requested_quantity": order["quantity"],
                "reason": order["reason"],
            }
            for order in rejected_orders
        ]
        if rejection_rows != transition["rejections"]:
            raise TransitionIntegrityError("rejected order rows do not match rejection records")

        fill_event_rows = [
            {key: item for key, item in payload.items() if key != "run_id"}
            for payload in event_payloads.get("order_filled", [])
        ]
        if fill_event_rows != transition["fills"]:
            raise TransitionIntegrityError("order_filled events do not match fill rows")
        if len(completions) != 1 or not events or events[-1] is not completions[0]:
            raise TransitionIntegrityError(
                "journal must end with exactly one rebalance_completed event"
            )
        if (
            _datetime(completions[0]["as_of"], "rebalance_completed.as_of").date().isoformat()
            != session
        ):
            raise TransitionIntegrityError("rebalance_completed as_of does not match session")

    def _validate(self, payload: Any, path: Path) -> dict[str, Any]:
        transition = _mapping(payload, f"journal {path.name}")
        _require(
            transition,
            {
                "schema_version",
                "run_id",
                "session",
                "target",
                "decisions",
                "orders",
                "rejections",
                "fills",
                "portfolio",
                "references",
                "equity",
                "events",
            },
            f"journal {path.name}",
        )
        if not isinstance(transition["schema_version"], int) or isinstance(
            transition["schema_version"], bool
        ):
            raise TransitionIntegrityError("journal schema_version must be an integer")
        if not isinstance(transition["run_id"], str) or not transition["run_id"]:
            raise TransitionIntegrityError("journal run_id must be a non-empty string")
        try:
            session = _session_key(transition["session"])
        except ValueError as exc:
            raise TransitionIntegrityError(str(exc)) from exc
        if session != path.stem:
            raise TransitionIntegrityError(f"journal session does not match filename: {path.name}")
        if self.schema_version is not None and transition["schema_version"] != self.schema_version:
            raise TransitionIntegrityError(f"journal schema_version mismatch: {path.name}")
        if self.run_id is not None and transition["run_id"] != self.run_id:
            raise TransitionIntegrityError(f"journal run_id mismatch: {path.name}")

        try:
            target = TargetPortfolio.model_validate(_mapping(transition["target"], "target"))
            portfolio = PortfolioSnapshot.model_validate(
                {"run_id": transition["run_id"], **_mapping(transition["portfolio"], "portfolio")}
            )
        except (ValidationError, TypeError) as exc:
            raise TransitionIntegrityError(f"invalid domain shape in {path.name}: {exc}") from exc
        if target.run_id != transition["run_id"]:
            raise TransitionIntegrityError("target run_id mismatch")
        if portfolio.run_id != transition["run_id"]:
            raise TransitionIntegrityError("portfolio run_id mismatch")
        if (
            portfolio.cash + sum((position.value for position in portfolio.positions), Decimal(0))
            != portfolio.total_equity
        ):
            raise TransitionIntegrityError("portfolio total_equity is inconsistent")
        if portfolio.as_of.date().isoformat() != session:
            raise TransitionIntegrityError("portfolio as_of does not match session")

        self._validate_decisions(transition["decisions"])
        self._validate_orders(transition["orders"], session)
        self._validate_rejections(transition["rejections"])
        for index, raw in enumerate(_list(transition["fills"], "fills")):
            fill = _mapping(raw, f"fills[{index}]")
            try:
                parsed_fill = FillEvent.model_validate({"run_id": transition["run_id"], **fill})
            except ValidationError as exc:
                raise TransitionIntegrityError(f"invalid fills[{index}]: {exc}") from exc
            if parsed_fill.run_id != transition["run_id"]:
                raise TransitionIntegrityError(f"fills[{index}] run_id mismatch")
            if parsed_fill.filled_at.date().isoformat() != session:
                raise TransitionIntegrityError(f"fills[{index}].filled_at does not match session")

        references = _mapping(transition["references"], "references")
        _require(references, {"spy_equity", "equal_weight_equity"}, "references")
        _decimal(references["spy_equity"], "references.spy_equity")
        _decimal(references["equal_weight_equity"], "references.equal_weight_equity")
        equity = _mapping(transition["equity"], "equity")
        _require(
            equity,
            {"date", "equity", "spy_equity", "equal_weight_equity"},
            "equity",
        )
        if equity["date"] != session:
            raise TransitionIntegrityError("equity.date does not match session")
        for field in ("equity", "spy_equity", "equal_weight_equity"):
            _decimal(equity[field], f"equity.{field}")
        if _decimal(equity["spy_equity"], "equity.spy_equity") != _decimal(
            references["spy_equity"], "references.spy_equity"
        ) or _decimal(equity["equal_weight_equity"], "equity.equal_weight_equity") != _decimal(
            references["equal_weight_equity"], "references.equal_weight_equity"
        ):
            raise TransitionIntegrityError("equity benchmarks do not match references")
        if _decimal(equity["equity"], "equity.equity") != portfolio.total_equity:
            raise TransitionIntegrityError("equity does not match portfolio total_equity")
        self._validate_events(transition["events"], session, transition)
        return dict(transition)

    def read_all(self) -> list[dict[str, Any]]:
        """Read and validate committed journals in execution-session order."""
        if not self.directory.exists():
            return []
        journals = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TransitionIntegrityError(
                    f"cannot read transition journal {path.name}"
                ) from exc
            journals.append(self._validate(payload, path))
        return journals

    def completed_sessions(self) -> set[str]:
        return {transition["session"] for transition in self.read_all()}
