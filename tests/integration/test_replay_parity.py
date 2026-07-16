"""End-to-end stub-generator replay: replay-vs-forward parity, idempotency,
ledger reconstruction, and artifact shapes matching tests/fixtures/demo-run."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from retailtrader.evaluation.metrics import compute_evaluation, read_equity_csv
from retailtrader.evaluation.report import write_evaluation_json
from retailtrader.simulation.ledger import replay_events
from retailtrader.simulation.runner import ExperimentRunner
from retailtrader.storage.artifacts import read_jsonl
from retailtrader.storage.events import EventLog
from tests.helpers import close_dt, make_experiment, make_frame, open_dt, stub_generator

FIXTURE_RUN = Path(__file__).parents[2] / "tests/fixtures/demo-run/exp-trend-v1-2024"
PHILOSOPHY_YAML = "name: stub\nversion: v1\n"

DECISION_SESSIONS = [date(2024, 1, 5), date(2024, 1, 12), date(2024, 1, 19)]
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


def frames() -> list:
    return [
        make_frame(
            decision_session,
            execution_session,
            PRICES[execution_session],
            PRICES[execution_session],
        )
        for decision_session, execution_session in zip(DECISION_SESSIONS, SESSIONS, strict=True)
    ]


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

    make_runner(replay_dir).replay(frames())
    # Forward mode: a fresh runner per session, restoring state from the event
    # log, exactly as a daily paper-trading process restart would.
    for frame in frames():
        make_runner(forward_dir).step(frame)

    for name in ARTIFACTS:
        assert (replay_dir / name).read_bytes() == (forward_dir / name).read_bytes(), name
    assert events_without_created_at(replay_dir) == events_without_created_at(forward_dir)


def test_target_generator_receives_only_decision_snapshot(tmp_path: Path) -> None:
    decision_prices = PRICES[SESSIONS[0]]
    execution_prices = {symbol: ("9999.00", "8888.00") for symbol in decision_prices} | {
        "EXEC_ONLY": ("7777.00", "6666.00")
    }
    frame = make_frame(
        DECISION_SESSIONS[0],
        SESSIONS[0],
        decision_prices,
        execution_prices,
    )
    received = []

    def recording_generator(experiment, snapshot):
        received.append(snapshot)
        return stub_generator(experiment, snapshot)

    runner = ExperimentRunner(
        experiment=make_experiment(),
        run_dir=tmp_path,
        generate_target=recording_generator,
        benchmarks=BENCHMARKS,
        philosophy_yaml=PHILOSOPHY_YAML,
        initial_cash=Decimal("100000.00"),
    )
    runner.step(frame)

    assert received == [frame.decision]
    assert received[0] is frame.decision
    assert received[0].as_of == close_dt(DECISION_SESSIONS[0])
    assert {bar.session for bar in received[0].bars} == {DECISION_SESSIONS[0]}
    assert {
        bar.symbol: (str(bar.open), str(bar.close)) for bar in received[0].bars
    } == decision_prices
    assert "EXEC_ONLY" not in {bar.symbol for bar in received[0].bars}
    assert all(bar.open != Decimal("9999.00") for bar in received[0].bars)


def test_decision_execution_timestamps_and_open_price_sizing(tmp_path: Path) -> None:
    base_frame = frames()[0]
    changed_prices = {
        symbol: (str(Decimal(open_price) * 2), close_price)
        for symbol, (open_price, close_price) in PRICES[SESSIONS[0]].items()
    }
    changed_frame = make_frame(
        DECISION_SESSIONS[0],
        SESSIONS[0],
        PRICES[SESSIONS[0]],
        changed_prices,
    )

    make_runner(tmp_path / "base").step(base_frame)
    make_runner(tmp_path / "changed").step(changed_frame)

    base_events = EventLog(tmp_path / "base/events.jsonl", "run-test").read()
    changed_events = EventLog(tmp_path / "changed/events.jsonl", "run-test").read()
    base_quantities = [
        event["payload"]["quantity"]
        for event in base_events
        if event["event_type"] == "order_created"
    ]
    changed_quantities = [
        event["payload"]["quantity"]
        for event in changed_events
        if event["event_type"] == "order_created"
    ]
    assert base_quantities != changed_quantities

    assert [
        event["as_of"] for event in base_events if event["event_type"] == "target_generated"
    ] == [close_dt(DECISION_SESSIONS[0]).isoformat()]
    quantity_events = [
        event
        for event in base_events
        if event["event_type"] in {"order_created", "order_rejected", "order_filled"}
    ]
    assert quantity_events
    assert {event["as_of"] for event in quantity_events} == {open_dt(SESSIONS[0]).isoformat()}
    assert {
        event["as_of"]
        for event in base_events
        if event["event_type"] in {"portfolio_marked", "rebalance_completed"}
    } == {close_dt(SESSIONS[0]).isoformat()}


def test_target_timestamp_must_match_decision_close(tmp_path: Path) -> None:
    def mismatched_generator(experiment, snapshot):
        target, decisions = stub_generator(experiment, snapshot)
        return target.model_copy(update={"as_of": open_dt(SESSIONS[0])}), decisions

    runner = ExperimentRunner(
        experiment=make_experiment(),
        run_dir=tmp_path,
        generate_target=mismatched_generator,
        benchmarks=BENCHMARKS,
        philosophy_yaml=PHILOSOPHY_YAML,
        initial_cash=Decimal("100000.00"),
    )
    with pytest.raises(ValueError, match="target.as_of"):
        runner.step(frames()[0])


def test_same_execution_session_with_different_close_time_runs_once(
    tmp_path: Path,
) -> None:
    frame = frames()[0]
    later_close_frame = replace(
        frame,
        execution=frame.execution.model_copy(
            update={"as_of": frame.execution.as_of + timedelta(hours=1)}
        ),
    )
    runner = make_runner(tmp_path)

    runner.step(frame)
    runner.step(later_close_frame)

    events = runner.event_log.read()
    completions = [event for event in events if event["event_type"] == "rebalance_completed"]
    assert len(completions) == 1
    assert completions[0]["payload"]["session"] == SESSIONS[0].isoformat()
    assert len((tmp_path / "equity.csv").read_text().splitlines()) == 2


def test_repeated_session_processing_is_idempotent(tmp_path: Path) -> None:
    runner = make_runner(tmp_path)
    runner.replay(frames())
    before = {name: (tmp_path / name).read_bytes() for name in ARTIFACTS + ["events.jsonl"]}
    portfolio = runner.step(frames()[-1])  # same session again
    after = {name: (tmp_path / name).read_bytes() for name in ARTIFACTS + ["events.jsonl"]}
    assert before == after
    assert portfolio == runner.portfolio


def test_ledger_replay_reconstructs_the_final_portfolio(tmp_path: Path) -> None:
    runner = make_runner(tmp_path)
    final = runner.replay(frames())
    state = replay_events(runner.event_log.read())
    assert state.cash == final.cash
    assert state.positions == {p.symbol: p.quantity for p in final.positions}
    assert state.equity == final.total_equity
    assert final.cash >= 0


def test_artifact_shapes_match_the_frozen_fixture(tmp_path: Path) -> None:
    runner = make_runner(tmp_path)
    runner.replay(frames())

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
    runner.replay(frames())
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
