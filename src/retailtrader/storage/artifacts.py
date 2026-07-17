"""Run artifact writers. Shapes mirror tests/fixtures/demo-run exactly.

Per run directory:
    manifest.json    — ExperimentManifest, indented JSON
    philosophy.yaml  — passed through verbatim
    decisions.jsonl  — decision records from the target generator, verbatim
    orders.jsonl     — created and rejected order records
    fills.jsonl      — fill records, prices as decimal strings
    portfolio.jsonl  — marked portfolio per session (no run_id, fixture shape)
    equity.csv       — date,equity,synthetic_mega_cap_proxy_equity,
                       equal_weight_equity (2dp)

Money is serialized as decimal strings; timestamps as ISO-8601 UTC.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from retailtrader.domain import ExperimentManifest, FillEvent, PortfolioSnapshot
from retailtrader.storage.events import to_jsonable

EQUITY_HEADER = "date,equity,synthetic_mega_cap_proxy_equity,equal_weight_equity"
SPY_EQUITY_HEADER = "date,equity,spy_equity,equal_weight_equity"
SUPPORTED_EQUITY_HEADERS = frozenset({EQUITY_HEADER, SPY_EQUITY_HEADER})


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


def read_manifest(path: Path) -> ExperimentManifest:
    """Load and validate a persisted experiment manifest."""
    return ExperimentManifest.model_validate_json(path.read_text(encoding="utf-8"))


class RunWriter:
    """Writes one experiment's artifact set into a run directory."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.run_dir / name

    def write_manifest(self, manifest: ExperimentManifest) -> None:
        payload = to_jsonable(manifest.model_dump())
        self.path("manifest.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    def write_philosophy(self, yaml_text: str) -> None:
        self.path("philosophy.yaml").write_text(yaml_text, encoding="utf-8")

    def write_data_provenance(self, provenance: dict[str, Any]) -> None:
        self.path("data-provenance.json").write_text(
            json.dumps(to_jsonable(provenance), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def initialize_materialized(self, equity_header: str = EQUITY_HEADER) -> None:
        if equity_header not in SUPPORTED_EQUITY_HEADERS:
            raise ValueError(f"unsupported equity header: {equity_header}")
        for name in ("decisions.jsonl", "orders.jsonl", "fills.jsonl", "portfolio.jsonl"):
            self.path(name).write_text("", encoding="utf-8")
        self.path("equity.csv").write_text(equity_header + "\n", encoding="utf-8")

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
        synthetic_mega_cap_proxy_equity: Decimal,
        equal_weight_equity: Decimal,
    ) -> None:
        path = self.path("equity.csv")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"{session.isoformat()},{equity:.2f},"
                f"{synthetic_mega_cap_proxy_equity:.2f},{equal_weight_equity:.2f}\n"
            )
