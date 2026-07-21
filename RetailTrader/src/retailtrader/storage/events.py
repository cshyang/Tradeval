"""Append-only JSONL event log for portfolio simulation.

Every order, fill, and portfolio transition appends one envelope line:

    {"schema_version": 1, "run_id": ..., "event_type": ..., "as_of": ...,
     "created_at": ..., "payload": {...}}

Decimals serialize as strings, datetimes as ISO-8601 UTC. `created_at` is the
only non-deterministic field; parity comparisons must exclude it.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from retailtrader.domain import SCHEMA_VERSION

EVENT_TYPES = frozenset(
    {
        "portfolio_created",
        "target_generated",
        "order_created",
        "order_rejected",
        "order_filled",
        "portfolio_marked",
        "rebalance_completed",
    }
)


def to_jsonable(value: Any) -> Any:
    """Convert domain values to JSON-safe types (Decimal -> str, datetime -> ISO UTC)."""
    if isinstance(value, BaseModel):
        return to_jsonable(value.model_dump())
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


def event_record(
    run_id: str,
    event_type: str,
    as_of: datetime,
    payload: Any,
    created_at: datetime,
) -> dict[str, Any]:
    """Build an event envelope without mutating the public projection."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event type: {event_type}")
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "event_type": event_type,
        "as_of": to_jsonable(as_of),
        "created_at": to_jsonable(created_at),
        "payload": to_jsonable(payload),
    }


def replace_complete(path: Path, content: bytes) -> None:
    """Fsync a complete sibling file before atomically replacing ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class EventLog:
    """Append-only JSONL event log for one run."""

    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id

    def append(
        self,
        event_type: str,
        as_of: datetime,
        payload: Any,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        event = event_record(
            self.run_id,
            event_type,
            as_of,
            payload,
            created_at or datetime.now(UTC),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
        return event

    def materialize(
        self,
        initial_event: Mapping[str, Any],
        transitions: Sequence[Mapping[str, Any]],
        failure_hook: Callable[[str], None] | None = None,
    ) -> None:
        events = [initial_event]
        for transition in transitions:
            events.extend(transition["events"])
        content = "".join(json.dumps(event) + "\n" for event in events).encode()
        replace_complete(self.path, content)
        if failure_hook is not None:
            failure_hook(f"after_artifact_replace:{self.path.name}")

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def completed_sessions(self) -> set[str]:
        """ISO date keys of sessions already sealed by rebalance_completed."""
        return {
            event["payload"]["session"]
            for event in self.read()
            if event["event_type"] == "rebalance_completed"
        }
