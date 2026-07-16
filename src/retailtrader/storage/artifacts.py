"""Run artifact writers. Shapes mirror tests/fixtures/demo-run exactly.

Per run directory:
    manifest.json    — ExperimentManifest, indented JSON
    philosophy.yaml  — passed through verbatim
    decisions.jsonl  — decision records from the target generator, verbatim
    orders.jsonl     — created and rejected order records
    fills.jsonl      — fill records, prices as decimal strings
    portfolio.jsonl  — marked portfolio per session (no run_id, fixture shape)
    equity.csv       — date,equity,spy_equity,equal_weight_equity (2dp)

Money is serialized as decimal strings; timestamps as ISO-8601 UTC.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from retailtrader.domain import ExperimentManifest, FillEvent, PortfolioSnapshot
from retailtrader.storage.events import _replace_complete, to_jsonable
from retailtrader.storage.transitions import TransitionIntegrityError

EQUITY_HEADER = "date,equity,spy_equity,equal_weight_equity"


def portfolio_row(snapshot: PortfolioSnapshot) -> dict[str, Any]:
    """Fixture-shaped portfolio.jsonl line (run_id intentionally omitted)."""
    return {
        "as_of": to_jsonable(snapshot.as_of),
        "cash": str(snapshot.cash),
        "positions": [
            {
                "symbol": position.symbol,
                "quantity": position.quantity,
                "price": str(position.price),
                "value": str(position.value),
            }
            for position in snapshot.positions
        ],
        "total_equity": str(snapshot.total_equity),
    }


def fill_row(fill: FillEvent) -> dict[str, Any]:
    return {
        "symbol": fill.symbol,
        "side": fill.side,
        "quantity": fill.quantity,
        "fill_price": str(fill.fill_price),
        "filled_at": to_jsonable(fill.filled_at),
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class RunWriter:
    """Writes one experiment's artifact set into a run directory."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.run_dir / name

    def _fsync_run_directory(self) -> None:
        descriptor = os.open(self.run_dir, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write_durable(self, name: str, content: bytes) -> None:
        _replace_complete(self.path(name), content)
        self._fsync_run_directory()

    def write_manifest(self, manifest: ExperimentManifest) -> None:
        payload = to_jsonable(manifest.model_dump())
        self._write_durable("manifest.json", (json.dumps(payload, indent=2) + "\n").encode())

    def write_philosophy(self, yaml_text: str) -> None:
        self._write_durable("philosophy.yaml", yaml_text.encode())

    def ensure_run_metadata(self, manifest: ExperimentManifest, philosophy_yaml: str) -> None:
        """Validate immutable run identity, then atomically heal missing files."""
        manifest_path = self.path("manifest.json")
        philosophy_path = self.path("philosophy.yaml")
        if manifest_path.exists():
            try:
                existing_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                existing = ExperimentManifest.model_validate(existing_payload)
            except (
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                ValidationError,
            ) as exc:
                raise TransitionIntegrityError("invalid manifest.json") from exc
            stable_existing = existing.model_dump(exclude={"created_at"})
            stable_expected = manifest.model_dump(exclude={"created_at"})
            if stable_existing != stable_expected:
                raise TransitionIntegrityError("immutable manifest identity mismatch")
        if philosophy_path.exists():
            try:
                existing_philosophy = philosophy_path.read_bytes()
            except OSError as exc:
                raise TransitionIntegrityError("invalid philosophy.yaml") from exc
            if existing_philosophy != philosophy_yaml.encode():
                raise TransitionIntegrityError("immutable philosophy.yaml mismatch")

        if not manifest_path.exists():
            self.write_manifest(manifest)
        if not philosophy_path.exists():
            self.write_philosophy(philosophy_yaml)
        # Existing entries may themselves be visible from an interrupted write.
        self._fsync_run_directory()

    def _append_jsonl(self, name: str, record: dict[str, Any]) -> None:
        with self.path(name).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(to_jsonable(record)) + "\n")

    def append_decision(self, record: dict[str, Any]) -> None:
        self._append_jsonl("decisions.jsonl", record)

    def append_order(self, record: dict[str, Any]) -> None:
        self._append_jsonl("orders.jsonl", record)

    def append_fill(self, fill: FillEvent) -> None:
        self._append_jsonl("fills.jsonl", fill_row(fill))

    def append_portfolio(self, snapshot: PortfolioSnapshot) -> None:
        self._append_jsonl("portfolio.jsonl", portfolio_row(snapshot))

    def append_equity_row(
        self,
        session: date,
        equity: Decimal,
        spy_equity: Decimal,
        equal_weight_equity: Decimal,
    ) -> None:
        path = self.path("equity.csv")
        if not path.exists():
            path.write_text(EQUITY_HEADER + "\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"{session.isoformat()},{equity:.2f},{spy_equity:.2f},{equal_weight_equity:.2f}\n"
            )

    def materialize(
        self,
        transitions: Sequence[Mapping[str, Any]],
        failure_hook: Callable[[str], None] | None = None,
    ) -> None:
        """Atomically replace all public artifacts projected from journals."""
        projections: dict[str, list[Mapping[str, Any]]] = {
            "decisions.jsonl": [],
            "orders.jsonl": [],
            "fills.jsonl": [],
            "portfolio.jsonl": [],
        }
        equity_lines = [EQUITY_HEADER]
        for transition in transitions:
            projections["decisions.jsonl"].extend(transition["decisions"])
            projections["orders.jsonl"].extend(transition["orders"])
            projections["fills.jsonl"].extend(transition["fills"])
            projections["portfolio.jsonl"].append(transition["portfolio"])
            equity = transition["equity"]
            equity_lines.append(
                f"{equity['date']},{Decimal(equity['equity']):.2f},"
                f"{Decimal(equity['spy_equity']):.2f},"
                f"{Decimal(equity['equal_weight_equity']):.2f}"
            )

        for name, records in projections.items():
            content = "".join(json.dumps(record) + "\n" for record in records).encode()
            _replace_complete(self.path(name), content)
            if failure_hook is not None:
                failure_hook(f"after_artifact_replace:{name}")

        equity_content = ("\n".join(equity_lines) + "\n").encode()
        _replace_complete(self.path("equity.csv"), equity_content)
        if failure_hook is not None:
            failure_hook("after_artifact_replace:equity.csv")
