from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from retailtrader.cli import app
from retailtrader.simulation.ledger import replay_events
from retailtrader.storage.artifacts import read_jsonl


def test_demo_builds_three_distinct_auditable_experiments(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["demo", "--workspace", str(tmp_path), "--format", "json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)["result"]
    assert len(payload["experiments"]) == 3
    assert (tmp_path / "comparison.md").is_file()
    selected = []
    for summary in payload["experiments"]:
        run_dir = tmp_path / summary["run_id"]
        assert {
            "manifest.json",
            "philosophy.yaml",
            "events.jsonl",
            "decisions.jsonl",
            "orders.jsonl",
            "fills.jsonl",
            "portfolio.jsonl",
            "equity.csv",
            "evaluation.json",
            "report.md",
        }.issubset({path.name for path in run_dir.iterdir()})
        state = replay_events(read_jsonl(run_dir / "events.jsonl"))
        final = read_jsonl(run_dir / "portfolio.jsonl")[-1]
        assert str(state.equity) == final["total_equity"]
        selected.append(
            tuple(row["symbol"] for row in read_jsonl(run_dir / "decisions.jsonl")[-1]["selected"])
        )
    assert len(set(selected)) > 1

    trend_dir = tmp_path / "exp-trend-v1"
    events = read_jsonl(trend_dir / "events.jsonl")
    created = events[0]
    assert created["payload"]["max_turnover"] == 0.5

    notional_by_session: dict[str, Decimal] = defaultdict(Decimal)
    for event in events:
        if event["event_type"] == "order_filled":
            payload = event["payload"]
            notional_by_session[event["as_of"]] += Decimal(payload["fill_price"]) * int(
                payload["quantity"]
            )
    opening_equity = Decimal(created["payload"]["cash"])
    for event in events:
        if event["event_type"] != "portfolio_marked":
            continue
        turnover = notional_by_session[event["as_of"]] / 2 / opening_equity
        assert turnover <= Decimal("0.5")
        opening_equity = Decimal(event["payload"]["total_equity"])
