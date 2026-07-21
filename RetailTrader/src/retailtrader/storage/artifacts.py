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
import os
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from retailtrader.domain import ExperimentManifest, PortfolioSnapshot
from retailtrader.storage.events import replace_complete, to_jsonable

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

    def _write_durable(self, name: str, content: bytes) -> None:
        replace_complete(self.path(name), content)
        descriptor = os.open(self.run_dir, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def write_manifest(self, manifest: ExperimentManifest) -> None:
        payload = to_jsonable(manifest.model_dump())
        self._write_durable(
            "manifest.json", (json.dumps(payload, indent=2) + "\n").encode()
        )

    def write_philosophy(self, yaml_text: str) -> None:
        self._write_durable("philosophy.yaml", yaml_text.encode())

    def write_data_provenance(self, provenance: dict[str, Any]) -> None:
        self._write_durable(
            "data-provenance.json",
            (json.dumps(to_jsonable(provenance), indent=2, sort_keys=True) + "\n").encode(),
        )

    def materialize(
        self,
        transitions: Sequence[Mapping[str, Any]],
        equity_header: str,
        failure_hook: Callable[[str], None] | None = None,
    ) -> None:
        """Atomically replace every public projection from committed journals."""
        if equity_header not in SUPPORTED_EQUITY_HEADERS:
            raise ValueError(f"unsupported equity header: {equity_header}")
        reference_column = equity_header.split(",")[2]
        projections: dict[str, list[dict[str, Any]]] = {
            "decisions.jsonl": [],
            "orders.jsonl": [],
            "fills.jsonl": [],
            "portfolio.jsonl": [],
        }
        equity_lines = [equity_header]
        for transition in transitions:
            if transition["reference_column"] != reference_column:
                raise ValueError("transition reference column does not match equity header")
            for event in transition["events"]:
                event_type = event["event_type"]
                payload = event["payload"]
                if event_type == "target_generated":
                    projections["decisions.jsonl"].extend(payload["decisions"])
                elif event_type == "order_created":
                    projections["orders.jsonl"].append(
                        {
                            "as_of": payload["as_of"],
                            "symbol": payload["symbol"],
                            "side": payload["side"],
                            "quantity": payload["quantity"],
                            "status": "created",
                            "reason": None,
                        }
                    )
                elif event_type == "order_rejected":
                    projections["orders.jsonl"].append(payload)
                elif event_type == "order_filled":
                    projections["fills.jsonl"].append(
                        {key: value for key, value in payload.items() if key != "run_id"}
                    )
                elif event_type == "portfolio_marked":
                    projections["portfolio.jsonl"].append(payload)
                    equity_lines.append(
                        f"{transition['session']},{Decimal(payload['total_equity']):.2f},"
                        f"{Decimal(transition['reference_equity']):.2f},"
                        f"{Decimal(transition['equal_weight_equity']):.2f}"
                    )

        for name, records in projections.items():
            content = "".join(json.dumps(record) + "\n" for record in records).encode()
            replace_complete(self.path(name), content)
            if failure_hook is not None:
                failure_hook(f"after_artifact_replace:{name}")
        replace_complete(self.path("equity.csv"), ("\n".join(equity_lines) + "\n").encode())
        if failure_hook is not None:
            failure_hook("after_artifact_replace:equity.csv")
