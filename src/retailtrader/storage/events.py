"""Append-only JSONL event log for portfolio simulation.

Every order, fill, and portfolio transition appends one envelope line:

    {"schema_version": 1, "run_id": ..., "event_type": ..., "as_of": ...,
     "created_at": ..., "payload": {...}}

Decimals serialize as strings, datetimes as ISO-8601 UTC. `created_at` is the
only non-deterministic field; parity comparisons must exclude it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
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
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {event_type}")
        event = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "event_type": event_type,
            "as_of": to_jsonable(as_of),
            "created_at": to_jsonable(created_at or datetime.now(UTC)),
            "payload": to_jsonable(payload),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
        return event

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def completed_sessions(self) -> set[str]:
        """ISO `as_of` values of sessions already sealed by rebalance_completed."""
        return {
            event["as_of"]
            for event in self.read()
            if event["event_type"] == "rebalance_completed"
        }
