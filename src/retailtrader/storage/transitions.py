"""Crash-safe immutable journals for completed simulation transitions."""

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

from retailtrader.storage.events import EVENT_TYPES

FailureHook = Callable[[str], None]


class TransitionIntegrityError(RuntimeError):
    """A transition journal or immutable run state is invalid or conflicting."""


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _session_key(session: date | str) -> str:
    value = session.isoformat() if isinstance(session, date) else session
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid execution session: {value}") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"execution session must be an ISO date: {value}")
    return value


def _aware_datetime(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise TransitionIntegrityError(f"{label} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TransitionIntegrityError(f"{label} must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TransitionIntegrityError(f"{label} must be timezone-aware")
    return parsed


def _finite_decimal(value: Any, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TransitionIntegrityError(f"{label} must be numeric") from exc
    if not parsed.is_finite():
        raise TransitionIntegrityError(f"{label} must be finite")
    return parsed


class TransitionStore:
    """Own one atomically committed source-of-truth journal per session."""

    def __init__(
        self,
        run_dir: Path,
        failure_hook: FailureHook | None = None,
        *,
        run_id: str | None = None,
        schema_version: int | None = None,
        reference_column: str | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.directory = run_dir / "transitions"
        self.state_path = run_dir / "run-state.json"
        self.failure_hook = failure_hook
        self.run_id = run_id
        self.schema_version = schema_version
        self.reference_column = reference_column

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Serialize startup and transitions across threads and processes."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.run_dir, os.O_RDONLY)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _fail(self, point: str) -> None:
        if self.failure_hook is not None:
            self.failure_hook(point)

    def _fsync_directory(self, path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _publish_once(self, target: Path, content: bytes, prefix: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        created_entry_parent = self.run_dir if target.parent == self.directory else target.parent
        self._fsync_directory(created_entry_parent)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=target.parent, prefix=prefix, delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            self._fail("before_journal_replace")
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() != content:
                    raise TransitionIntegrityError(
                        f"conflicting immutable file: {target.name}"
                    ) from None
            temporary.unlink()
            temporary = None
            self._fail("after_journal_replace")
            self._fail("before_parent_fsync")
            self._fsync_directory(target.parent)
            self._fail("after_parent_fsync")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def expected_state(
        self,
        *,
        created_as_of: datetime,
        initial_cash: Decimal,
        slippage_bps: int,
        max_turnover: float | None,
    ) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "created_as_of": created_as_of.isoformat(),
            "initial_cash": str(initial_cash),
            "slippage_bps": slippage_bps,
            "max_turnover": max_turnover,
            "reference_column": self.reference_column,
        }

    def initialize_state(
        self,
        *,
        created_as_of: datetime,
        initial_cash: Decimal,
        slippage_bps: int,
        max_turnover: float | None,
    ) -> dict[str, Any]:
        expected = self.expected_state(
            created_as_of=created_as_of,
            initial_cash=initial_cash,
            slippage_bps=slippage_bps,
            max_turnover=max_turnover,
        )
        self._publish_once(self.state_path, _canonical_bytes(expected), ".run-state.")
        return expected

    def read_state(
        self,
        *,
        created_as_of: datetime,
        initial_cash: Decimal,
        slippage_bps: int,
        max_turnover: float | None,
    ) -> dict[str, Any]:
        if not self.state_path.is_file():
            raise TransitionIntegrityError("missing immutable run-state.json")
        expected = self.expected_state(
            created_as_of=created_as_of,
            initial_cash=initial_cash,
            slippage_bps=slippage_bps,
            max_turnover=max_turnover,
        )
        try:
            existing = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TransitionIntegrityError("invalid immutable run-state.json") from exc
        if existing != expected:
            differing = next(
                (key for key in expected if existing.get(key) != expected[key]),
                "shape",
            )
            raise TransitionIntegrityError(f"run-state {differing} mismatch")
        return existing

    def path(self, session: date | str) -> Path:
        return self.directory / f"{_session_key(session)}.json"

    def _validate(self, payload: Any, path: Path) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TransitionIntegrityError(f"journal {path.name} must be an object")
        required = {
            "schema_version",
            "run_id",
            "session",
            "reference_column",
            "reference_equity",
            "equal_weight_equity",
            "events",
        }
        if set(payload) != required:
            raise TransitionIntegrityError(f"journal {path.name} has an invalid shape")
        try:
            session = _session_key(payload["session"])
        except ValueError as exc:
            raise TransitionIntegrityError(str(exc)) from exc
        if session != path.stem:
            raise TransitionIntegrityError(f"journal session does not match filename: {path.name}")
        if self.run_id is not None and payload["run_id"] != self.run_id:
            raise TransitionIntegrityError(f"journal run_id mismatch: {path.name}")
        if self.schema_version is not None and payload["schema_version"] != self.schema_version:
            raise TransitionIntegrityError(f"journal schema_version mismatch: {path.name}")
        if self.reference_column is not None and payload["reference_column"] != self.reference_column:
            raise TransitionIntegrityError(f"journal reference_column mismatch: {path.name}")
        _finite_decimal(payload["reference_equity"], "reference_equity")
        _finite_decimal(payload["equal_weight_equity"], "equal_weight_equity")

        events = payload["events"]
        if not isinstance(events, list) or not events:
            raise TransitionIntegrityError("journal events must be a non-empty list")
        required_event_keys = {
            "schema_version",
            "run_id",
            "event_type",
            "as_of",
            "created_at",
            "payload",
        }
        for index, event in enumerate(events):
            if not isinstance(event, dict) or set(event) != required_event_keys:
                raise TransitionIntegrityError(f"journal event {index} has an invalid envelope")
            if event["run_id"] != payload["run_id"]:
                raise TransitionIntegrityError(f"journal event {index} run_id mismatch")
            if event["schema_version"] != payload["schema_version"]:
                raise TransitionIntegrityError(f"journal event {index} schema_version mismatch")
            if event["event_type"] not in EVENT_TYPES - {"portfolio_created"}:
                raise TransitionIntegrityError(f"journal event {index} has an invalid event_type")
            _aware_datetime(event["as_of"], f"journal event {index} as_of")
            _aware_datetime(event["created_at"], f"journal event {index} created_at")
            if not isinstance(event["payload"], dict):
                raise TransitionIntegrityError(f"journal event {index} payload must be an object")
        completion = events[-1]
        if completion["event_type"] != "rebalance_completed" or completion["payload"] != {
            "session": session
        }:
            raise TransitionIntegrityError("journal must end with its rebalance_completed event")
        if _aware_datetime(completion["as_of"], "completion as_of").date().isoformat() != session:
            raise TransitionIntegrityError("completion timestamp does not match journal session")
        return payload

    def commit(self, session: date | str, transition: Mapping[str, Any]) -> None:
        session_key = _session_key(session)
        if transition.get("session") != session_key:
            raise ValueError("transition session does not match journal session")
        target = self.path(session_key)
        validated = self._validate(dict(transition), target)
        self._publish_once(target, _canonical_bytes(validated), f".{session_key}.")

    def ensure_durable(self) -> None:
        if self.directory.exists():
            self._fsync_directory(self.directory)

    def read_all(self) -> list[dict[str, Any]]:
        if not self.directory.exists():
            return []
        journals = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TransitionIntegrityError(f"cannot read transition journal {path.name}") from exc
            journals.append(self._validate(payload, path))
        return journals
