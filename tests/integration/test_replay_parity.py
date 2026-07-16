"""End-to-end stub-generator replay: replay-vs-forward parity, idempotency,
ledger reconstruction, and artifact shapes matching tests/fixtures/demo-run."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from retailtrader.evaluation.metrics import compute_evaluation, read_equity_csv
from retailtrader.evaluation.report import write_evaluation_json
from retailtrader.simulation.ledger import replay_events
from retailtrader.simulation.runner import ExperimentRunner
from retailtrader.storage.artifacts import read_jsonl
from retailtrader.storage.events import EventLog
from tests.helpers import make_experiment, make_snapshot, stub_generator

FIXTURE_RUN = Path(__file__).parents[2] / "tests/fixtures/demo-run/exp-trend-v1-2024"
PHILOSOPHY_YAML = "name: stub\nversion: v1\n"

SESSIONS = [date(2024, 1, 8), date(2024, 1, 15), date(2024, 1, 22)]
PRICES = {
    SESSIONS[0]: {
        "AAA": ("10.00", "10.50"),
        "BBB": ("20.00", "19.80"),
        "CCC": ("30.00", "30.90"),
        "DDD": ("40.00", "40.40"),
    },
    SESSIONS[1]: {
        "AAA": ("10.60", "10.40"),
        "BBB": ("19.90", "20.30"),
        "CCC": ("31.00", "30.50"),
        "DDD": ("40.80", "41.20"),
    },
    SESSIONS[2]: {
        "AAA": ("10.30", "10.70"),
        "BBB": ("20.40", "20.10"),
        "CCC": ("30.40", "31.10"),
        "DDD": ("41.50", "41.00"),
    },
}
BENCHMARKS = {
    SESSIONS[0]: (Decimal("100000.00"), Decimal("100000.00")),
    SESSIONS[1]: (Decimal("101000.00"), Decimal("100500.00")),
    SESSIONS[2]: (Decimal("102000.00"), Decimal("100800.00")),
}
ARTIFACTS = [
    "manifest.json",
    "philosophy.yaml",
    "decisions.jsonl",
    "orders.jsonl",
    "fills.jsonl",
    "portfolio.jsonl",
    "equity.csv",
]


def snapshots() -> list:
    return [make_snapshot(session, PRICES[session]) for session in SESSIONS]


def make_runner(run_dir: Path) -> ExperimentRunner:
    return ExperimentRunner(
        experiment=make_experiment(),
        run_dir=run_dir,
        generate_target=stub_generator,
        benchmarks=BENCHMARKS,
        philosophy_yaml=PHILOSOPHY_YAML,
        initial_cash=Decimal("100000.00"),
        slippage_bps=10,
    )


def events_without_created_at(run_dir: Path) -> list[str]:
    events = EventLog(run_dir / "events.jsonl", "run-test").read()
    return [
        json.dumps({key: value for key, value in event.items() if key != "created_at"})
        for event in events
    ]


def test_replay_and_forward_paper_produce_identical_artifacts(tmp_path: Path) -> None:
    replay_dir = tmp_path / "replay"
    forward_dir = tmp_path / "forward"

    make_runner(replay_dir).replay(snapshots())
    # Forward mode: a fresh runner per session, restoring state from the event
    # log, exactly as a daily paper-trading process restart would.
    for snapshot in snapshots():
        make_runner(forward_dir).step(snapshot)

    for name in ARTIFACTS:
        assert (replay_dir / name).read_bytes() == (forward_dir / name).read_bytes(), name
    assert events_without_created_at(replay_dir) == events_without_created_at(forward_dir)


def test_repeated_session_processing_is_idempotent(tmp_path: Path) -> None:
    runner = make_runner(tmp_path)
    runner.replay(snapshots())
    before = {name: (tmp_path / name).read_bytes() for name in ARTIFACTS + ["events.jsonl"]}
    portfolio = runner.step(snapshots()[-1])  # same session again
    after = {name: (tmp_path / name).read_bytes() for name in ARTIFACTS + ["events.jsonl"]}
    assert before == after
    assert portfolio == runner.portfolio


def test_ledger_replay_reconstructs_the_final_portfolio(tmp_path: Path) -> None:
    runner = make_runner(tmp_path)
    final = runner.replay(snapshots())
    state = replay_events(runner.event_log.read())
    assert state.cash == final.cash
    assert state.positions == {p.symbol: p.quantity for p in final.positions}
    assert state.equity == final.total_equity
    assert final.cash >= 0


def test_artifact_shapes_match_the_frozen_fixture(tmp_path: Path) -> None:
    runner = make_runner(tmp_path)
    runner.replay(snapshots())

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    fixture_manifest = json.loads((FIXTURE_RUN / "manifest.json").read_text())
    assert set(manifest) == set(fixture_manifest)

    decision = read_jsonl(tmp_path / "decisions.jsonl")[0]
    fixture_decision = read_jsonl(FIXTURE_RUN / "decisions.jsonl")[0]
    assert set(decision) == set(fixture_decision)
    assert set(decision["selected"][0]) == set(fixture_decision["selected"][0])
    assert set(decision["selected"][0]["factors"][0]) == set(
        fixture_decision["selected"][0]["factors"][0]
    )
    assert set(decision["rejected"][0]) == set(fixture_decision["rejected"][0])

    row = read_jsonl(tmp_path / "portfolio.jsonl")[0]
    fixture_row = read_jsonl(FIXTURE_RUN / "portfolio.jsonl")[0]
    assert set(row) == set(fixture_row)
    assert set(row["positions"][0]) == set(fixture_row["positions"][0])
    assert isinstance(row["cash"], str)  # Decimal-as-string
    assert isinstance(row["positions"][0]["price"], str)
    assert isinstance(row["positions"][0]["quantity"], int)
    assert row["as_of"].endswith("+00:00")  # ISO-8601 UTC

    header = (tmp_path / "equity.csv").read_text().splitlines()[0]
    fixture_header = (FIXTURE_RUN / "equity.csv").read_text().splitlines()[0]
    assert header == fixture_header

    for name in ("orders.jsonl", "fills.jsonl"):
        assert (tmp_path / name).exists(), name


def test_end_to_end_evaluation_from_replayed_artifacts(tmp_path: Path) -> None:
    runner = make_runner(tmp_path)
    runner.replay(snapshots())
    orders = read_jsonl(tmp_path / "orders.jsonl")
    report = compute_evaluation(
        run_id="run-test",
        as_of=datetime.combine(SESSIONS[-1], datetime.min.time(), tzinfo=UTC),
        equity=read_equity_csv(tmp_path / "equity.csv"),
        fills=read_jsonl(tmp_path / "fills.jsonl"),
        portfolios=read_jsonl(tmp_path / "portfolio.jsonl"),
        decisions=read_jsonl(tmp_path / "decisions.jsonl"),
        constraint_interventions=sum(order["status"] == "rejected" for order in orders),
    )
    write_evaluation_json(report, tmp_path / "evaluation.json")

    written = json.loads((tmp_path / "evaluation.json").read_text())
    fixture = json.loads((FIXTURE_RUN / "evaluation.json").read_text())
    assert set(written) == set(fixture)
    assert set(written["metrics"]) == set(fixture["metrics"])
    assert set(written["fidelity"]) == set(fixture["fidelity"])
    # The stub rotates between two overlapping picks each week.
    assert 0 < report.fidelity.selection_stability < 1
    assert report.metrics.trade_count > 0
