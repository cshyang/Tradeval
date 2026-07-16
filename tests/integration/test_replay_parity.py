"""End-to-end stub-generator replay: replay-vs-forward parity, idempotency,
ledger reconstruction, and artifact shapes matching tests/fixtures/demo-run."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import retailtrader.simulation.runner as runner_module
import retailtrader.storage.events as event_module
import retailtrader.storage.transitions as transition_module
from retailtrader.evaluation.metrics import compute_evaluation, read_equity_csv
from retailtrader.evaluation.report import write_evaluation_json
from retailtrader.simulation.ledger import replay_events
from retailtrader.simulation.runner import ExperimentRunner
from retailtrader.storage.artifacts import read_jsonl
from retailtrader.storage.events import EventLog
from retailtrader.storage.transitions import TransitionIntegrityError
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


def make_runner(
    run_dir: Path, failure_hook: Callable[[str], None] | None = None
) -> ExperimentRunner:
    return ExperimentRunner(
        experiment=make_experiment(),
        run_dir=run_dir,
        generate_target=stub_generator,
        benchmarks=BENCHMARKS,
        philosophy_yaml=PHILOSOPHY_YAML,
        initial_cash=Decimal("100000.00"),
        slippage_bps=10,
        failure_hook=failure_hook,
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


@pytest.mark.parametrize(
    "artifact",
    [
        "events.jsonl",
        "decisions.jsonl",
        "orders.jsonl",
        "fills.jsonl",
        "portfolio.jsonl",
        "equity.csv",
    ],
)
def test_restart_recovers_after_every_derived_artifact_replacement(
    tmp_path: Path, artifact: str
) -> None:
    run_dir = tmp_path / artifact

    def fail(point: str) -> None:
        if point == f"after_artifact_replace:{artifact}":
            raise OSError(f"injected after {artifact}")

    with pytest.raises(OSError, match="injected"):
        make_runner(run_dir, fail).step(frames()[0])

    assert (run_dir / f"transitions/{SESSIONS[0].isoformat()}.json").exists()
    recovered = make_runner(run_dir)
    expected = {name: (run_dir / name).read_bytes() for name in ARTIFACTS + ["events.jsonl"]}
    assert len(read_jsonl(run_dir / "fills.jsonl")) == len(
        json.loads((run_dir / f"transitions/{SESSIONS[0].isoformat()}.json").read_text())["fills"]
    )
    assert len(recovered.event_log.completed_sessions()) == 1

    make_runner(run_dir)
    actual = {name: (run_dir / name).read_bytes() for name in expected}
    assert actual == expected


@pytest.mark.parametrize(
    "failure_point",
    [
        "before_journal_replace",
        "after_journal_replace",
        "before_parent_fsync",
        "after_parent_fsync",
    ],
)
def test_restart_accepts_atomic_journal_outcome_and_materializes_once(
    tmp_path: Path, failure_point: str
) -> None:
    def fail(point: str) -> None:
        if point == failure_point:
            raise OSError(f"injected at {point}")

    with pytest.raises(OSError, match="injected"):
        make_runner(tmp_path, fail).step(frames()[0])

    recovered = make_runner(tmp_path)
    recovered.step(frames()[0])
    assert len(list((tmp_path / "transitions").glob("*.json"))) == 1
    journal = json.loads(next((tmp_path / "transitions").glob("*.json")).read_text())
    assert read_jsonl(tmp_path / "fills.jsonl") == journal["fills"]
    assert len(recovered.event_log.completed_sessions()) == 1


def test_recovery_fsyncs_visible_journal_before_artifact_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_before_directory_fsync(point: str) -> None:
        if point == "before_parent_fsync":
            raise OSError("injected before transition directory fsync")

    with pytest.raises(OSError, match="injected"):
        make_runner(tmp_path, fail_before_directory_fsync).step(frames()[0])

    actions: list[str] = []
    transition_inode = (tmp_path / "transitions").stat().st_ino
    real_fsync = transition_module.os.fsync
    real_replace = event_module.os.replace

    def recording_fsync(descriptor: int) -> None:
        if transition_module.os.fstat(descriptor).st_ino == transition_inode:
            actions.append("transition-directory-fsync")
        real_fsync(descriptor)

    def recording_replace(source: Path, target: Path) -> None:
        actions.append(f"artifact-replace:{Path(target).name}")
        real_replace(source, target)

    monkeypatch.setattr(transition_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(event_module.os, "replace", recording_replace)

    make_runner(tmp_path)

    first_fsync = actions.index("transition-directory-fsync")
    first_replace = next(
        index for index, action in enumerate(actions) if action.startswith("artifact-replace:")
    )
    assert first_fsync < first_replace


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


def test_same_runner_retry_heals_post_commit_materialization_failure(
    tmp_path: Path,
) -> None:
    failed = False

    def fail_once(point: str) -> None:
        nonlocal failed
        if point == "after_artifact_replace:events.jsonl" and not failed:
            failed = True
            raise OSError("injected after events")

    runner = make_runner(tmp_path, fail_once)
    with pytest.raises(OSError, match="injected"):
        runner.step(frames()[0])

    restored = runner.step(frames()[0])
    journal = json.loads((tmp_path / f"transitions/{SESSIONS[0].isoformat()}.json").read_text())
    assert restored == runner.portfolio
    assert restored.cash == Decimal(journal["portfolio"]["cash"])
    assert read_jsonl(tmp_path / "portfolio.jsonl") == [journal["portfolio"]]
    assert len(runner.event_log.completed_sessions()) == 1


def test_out_of_order_new_session_is_rejected(tmp_path: Path) -> None:
    runner = make_runner(tmp_path)
    runner.step(frames()[1])
    before = {name: (tmp_path / name).read_bytes() for name in ARTIFACTS + ["events.jsonl"]}

    with pytest.raises(TransitionIntegrityError, match="not later than latest"):
        runner.step(frames()[0])

    after = {name: (tmp_path / name).read_bytes() for name in ARTIFACTS + ["events.jsonl"]}
    assert after == before


def test_distinct_stale_runners_serialize_and_restore_canonical_portfolio(
    tmp_path: Path,
) -> None:
    first_entered_commit = threading.Event()
    release_first = threading.Event()

    def block_first(point: str) -> None:
        if point == "before_journal_replace":
            first_entered_commit.set()
            if not release_first.wait(timeout=5):
                raise TimeoutError("first transition was not released")

    first = make_runner(tmp_path, block_first)
    second = make_runner(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first.step, frames()[0])
        assert first_entered_commit.wait(timeout=5)
        second_future = executor.submit(second.step, frames()[1])
        try:
            assert not second_future.done()
        finally:
            release_first.set()
        first_future.result(timeout=5)
        concurrent_final = second_future.result(timeout=5)

    expected_dir = tmp_path / "expected"
    expected_final = make_runner(expected_dir).replay(frames()[:2])
    assert concurrent_final == expected_final
    for name in ARTIFACTS:
        assert (tmp_path / name).read_bytes() == (expected_dir / name).read_bytes(), name

    expected_sessions = {session.isoformat() for session in SESSIONS[:2]}
    journals = {path.stem for path in (tmp_path / "transitions").glob("*.json")}
    completions = {
        event["payload"]["session"]
        for event in EventLog(tmp_path / "events.jsonl", "run-test").read()
        if event["event_type"] == "rebalance_completed"
    }
    portfolio_sessions = {
        datetime.fromisoformat(row["as_of"]).date().isoformat()
        for row in read_jsonl(tmp_path / "portfolio.jsonl")
    }
    equity_sessions = {
        line.split(",", maxsplit=1)[0]
        for line in (tmp_path / "equity.csv").read_text().splitlines()[1:]
    }
    assert journals == completions == portfolio_sessions == equity_sessions == expected_sessions


def test_same_runner_serializes_step_and_portfolio_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = make_runner(tmp_path)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_attempting = threading.Event()
    calls: list[date] = []

    def controlled_step(experiment, portfolio, frame, generate_target, **kwargs):
        calls.append(frame.execution_session)
        if len(calls) == 1:
            first_entered.set()
            if not release_first.wait(timeout=5):
                raise TimeoutError("first same-runner step was not released")
        return portfolio.model_copy(update={"as_of": frame.execution.as_of})

    def run_second():
        second_attempting.set()
        return runner.step(frames()[1])

    monkeypatch.setattr(runner_module, "step", controlled_step)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(runner.step, frames()[0])
        assert first_entered.wait(timeout=5)
        second = executor.submit(run_second)
        assert second_attempting.wait(timeout=5)
        try:
            assert calls == [SESSIONS[0]]
        finally:
            release_first.set()
        first.result(timeout=5)
        second_result = second.result(timeout=5)

    assert calls == SESSIONS[:2]
    assert runner.portfolio == second_result
    assert runner.portfolio.as_of == frames()[1].execution.as_of


@pytest.mark.parametrize(
    "event_type",
    [
        "target_generated",
        "order_created",
        "order_rejected",
        "order_filled",
        "portfolio_marked",
        "rebalance_completed",
    ],
)
def test_event_envelope_timestamp_mismatch_cannot_replace_public_artifacts(
    tmp_path: Path, event_type: str
) -> None:
    if event_type == "order_rejected":
        runner = ExperimentRunner(
            experiment=make_experiment(),
            run_dir=tmp_path,
            generate_target=stub_generator,
            benchmarks=BENCHMARKS,
            philosophy_yaml=PHILOSOPHY_YAML,
            initial_cash=Decimal("100000.00"),
            slippage_bps=1000,
        )
    else:
        runner = make_runner(tmp_path)
    runner.step(frames()[0])
    public_paths = [tmp_path / name for name in ARTIFACTS + ["events.jsonl"]]
    before = {path: path.read_bytes() for path in public_paths}
    journal_path = tmp_path / f"transitions/{SESSIONS[0].isoformat()}.json"
    journal = json.loads(journal_path.read_text())
    event = next(item for item in journal["events"] if item["event_type"] == event_type)
    event["as_of"] = (datetime.fromisoformat(event["as_of"]) + timedelta(minutes=1)).isoformat()
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(TransitionIntegrityError, match="payload timing"):
        make_runner(tmp_path)

    assert {path: path.read_bytes() for path in public_paths} == before


def test_event_timestamps_must_preserve_decision_execution_mark_order(
    tmp_path: Path,
) -> None:
    make_runner(tmp_path).step(frames()[0])
    public_paths = [tmp_path / name for name in ARTIFACTS + ["events.jsonl"]]
    before = {path: path.read_bytes() for path in public_paths}
    journal_path = tmp_path / f"transitions/{SESSIONS[0].isoformat()}.json"
    journal = json.loads(journal_path.read_text())
    execution_at = next(
        event["as_of"] for event in journal["events"] if event["event_type"] == "order_created"
    )
    journal["target"]["as_of"] = execution_at
    target_event = next(
        event for event in journal["events"] if event["event_type"] == "target_generated"
    )
    target_event["as_of"] = execution_at
    target_event["payload"]["as_of"] = execution_at
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(TransitionIntegrityError, match="decision, execution, mark"):
        make_runner(tmp_path)

    assert {path: path.read_bytes() for path in public_paths} == before


def test_coherent_forged_portfolio_is_rejected_by_ledger_reconstruction(
    tmp_path: Path,
) -> None:
    make_runner(tmp_path).step(frames()[0])
    public_paths = [tmp_path / name for name in ARTIFACTS + ["events.jsonl", "initial-state.json"]]
    before = {path: path.read_bytes() for path in public_paths}
    journal_path = tmp_path / f"transitions/{SESSIONS[0].isoformat()}.json"
    journal = json.loads(journal_path.read_text())
    forged_cash = str(Decimal(journal["portfolio"]["cash"]) + Decimal("1.00"))
    forged_equity = str(Decimal(journal["portfolio"]["total_equity"]) + Decimal("1.00"))
    journal["portfolio"]["cash"] = forged_cash
    journal["portfolio"]["total_equity"] = forged_equity
    journal["equity"]["equity"] = forged_equity
    marked = next(event for event in journal["events"] if event["event_type"] == "portfolio_marked")
    marked["payload"]["cash"] = forged_cash
    marked["payload"]["total_equity"] = forged_equity
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(TransitionIntegrityError, match="ledger reconstruction"):
        make_runner(tmp_path)

    assert {path: path.read_bytes() for path in public_paths} == before


@pytest.mark.parametrize(
    "projection",
    ["fill_price", "created_order_quantity", "rejection_symbol"],
)
def test_cross_projection_mismatch_cannot_replace_public_artifacts(
    tmp_path: Path, projection: str
) -> None:
    if projection == "rejection_symbol":
        runner = ExperimentRunner(
            experiment=make_experiment(),
            run_dir=tmp_path,
            generate_target=stub_generator,
            benchmarks=BENCHMARKS,
            philosophy_yaml=PHILOSOPHY_YAML,
            initial_cash=Decimal("100000.00"),
            slippage_bps=1000,
        )
    else:
        runner = make_runner(tmp_path)
    runner.step(frames()[0])
    public_paths = [tmp_path / name for name in ARTIFACTS + ["events.jsonl"]]
    before = {path: path.read_bytes() for path in public_paths}
    journal_path = tmp_path / f"transitions/{SESSIONS[0].isoformat()}.json"
    journal = json.loads(journal_path.read_text())

    if projection == "fill_price":
        journal["fills"][0]["fill_price"] = "999.00"
    elif projection == "created_order_quantity":
        created = next(
            event for event in journal["events"] if event["event_type"] == "order_created"
        )
        created["payload"]["quantity"] += 1
    else:
        assert journal["rejections"]
        journal["rejections"][0]["symbol"] = "MISMATCH"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(TransitionIntegrityError):
        make_runner(tmp_path)

    assert {path: path.read_bytes() for path in public_paths} == before


@pytest.mark.parametrize("orphan", ["fill", "created_order"])
def test_order_fill_bijection_corruption_cannot_replace_public_artifacts(
    tmp_path: Path, orphan: str
) -> None:
    make_runner(tmp_path).step(frames()[0])
    public_paths = [tmp_path / name for name in ARTIFACTS + ["events.jsonl"]]
    before = {path: path.read_bytes() for path in public_paths}
    journal_path = tmp_path / f"transitions/{SESSIONS[0].isoformat()}.json"
    journal = json.loads(journal_path.read_text())

    if orphan == "fill":
        created_index = next(
            index for index, order in enumerate(journal["orders"]) if order["status"] == "created"
        )
        journal["orders"].pop(created_index)
        created_event_index = next(
            index
            for index, event in enumerate(journal["events"])
            if event["event_type"] == "order_created"
        )
        journal["events"].pop(created_event_index)
    else:
        journal["fills"].pop(0)
        fill_event_index = next(
            index
            for index, event in enumerate(journal["events"])
            if event["event_type"] == "order_filled"
        )
        journal["events"].pop(fill_event_index)
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(TransitionIntegrityError, match="do not form a bijection"):
        make_runner(tmp_path)

    assert {path: path.read_bytes() for path in public_paths} == before


@pytest.mark.parametrize("corruption", ["missing_field", "cross_run"])
def test_invalid_journal_cannot_replace_any_public_artifact(
    tmp_path: Path, corruption: str
) -> None:
    make_runner(tmp_path).step(frames()[0])
    public_paths = [tmp_path / name for name in ARTIFACTS + ["events.jsonl"]]
    before = {path: path.read_bytes() for path in public_paths}
    journal_path = tmp_path / f"transitions/{SESSIONS[0].isoformat()}.json"
    journal = json.loads(journal_path.read_text())
    if corruption == "missing_field":
        del journal["portfolio"]["positions"]
    else:
        journal["run_id"] = "another-run"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(TransitionIntegrityError):
        make_runner(tmp_path)

    assert {path: path.read_bytes() for path in public_paths} == before


@pytest.mark.parametrize("corruption", ["malformed", "cross_run"])
def test_invalid_journal_in_empty_run_writes_no_public_files_or_metadata(
    tmp_path: Path, corruption: str
) -> None:
    source = tmp_path / "source"
    make_runner(source).step(frames()[0])
    journal = json.loads((source / f"transitions/{SESSIONS[0].isoformat()}.json").read_text())
    destination = tmp_path / "destination"
    transition_dir = destination / "transitions"
    transition_dir.mkdir(parents=True)
    if corruption == "malformed":
        del journal["portfolio"]["positions"]
        experiment = make_experiment()
    else:
        experiment = make_experiment("different-run")
    (transition_dir / f"{SESSIONS[0].isoformat()}.json").write_text(
        json.dumps(journal), encoding="utf-8"
    )

    with pytest.raises(TransitionIntegrityError):
        ExperimentRunner(
            experiment=experiment,
            run_dir=destination,
            generate_target=stub_generator,
            benchmarks=BENCHMARKS,
            philosophy_yaml=PHILOSOPHY_YAML,
            initial_cash=Decimal("100000.00"),
        )

    public_paths = [destination / name for name in ARTIFACTS + ["events.jsonl"]]
    assert not any(path.exists() for path in public_paths)
    assert not (destination / "initial-state.json").exists()


@pytest.mark.parametrize("mismatch", ["manifest.json", "philosophy.yaml"])
def test_existing_metadata_mismatch_creates_no_other_files(tmp_path: Path, mismatch: str) -> None:
    if mismatch == "manifest.json":
        content = json.dumps(make_experiment("different-run").model_dump(mode="json")).encode()
    else:
        content = b"different: true\n"
    (tmp_path / mismatch).write_bytes(content)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(TransitionIntegrityError):
        make_runner(tmp_path)

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


@pytest.mark.parametrize("missing", ["manifest.json", "philosophy.yaml"])
def test_missing_run_metadata_file_is_atomically_healed(
    tmp_path: Path, missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_runner(tmp_path).step(frames()[0])
    manifest_before = (tmp_path / "manifest.json").read_bytes()
    (tmp_path / missing).unlink()
    actions: list[str] = []
    run_inode = tmp_path.stat().st_ino
    real_fsync = event_module.os.fsync
    real_replace = event_module.os.replace

    def recording_fsync(descriptor: int) -> None:
        if event_module.os.fstat(descriptor).st_ino == run_inode:
            actions.append("run-directory-fsync")
        real_fsync(descriptor)

    def recording_replace(source: Path, target: Path) -> None:
        actions.append(f"replace:{Path(target).name}")
        real_replace(source, target)

    monkeypatch.setattr(event_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(event_module.os, "replace", recording_replace)
    make_runner(tmp_path)

    replacement = actions.index(f"replace:{missing}")
    parent_fsync = actions.index("run-directory-fsync", replacement)
    assert replacement < parent_fsync
    assert (tmp_path / "manifest.json").read_bytes() == manifest_before
    assert (tmp_path / "philosophy.yaml").read_bytes() == PHILOSOPHY_YAML.encode()


@pytest.mark.parametrize("corruption", ["manifest_json", "manifest_identity", "philosophy"])
def test_run_metadata_mismatch_precedes_derived_artifact_changes(
    tmp_path: Path, corruption: str
) -> None:
    make_runner(tmp_path).step(frames()[0])
    derived_names = [
        "events.jsonl",
        "decisions.jsonl",
        "orders.jsonl",
        "fills.jsonl",
        "portfolio.jsonl",
        "equity.csv",
    ]
    before = {name: (tmp_path / name).read_bytes() for name in derived_names}
    if corruption == "manifest_json":
        (tmp_path / "manifest.json").write_text("{", encoding="utf-8")
    elif corruption == "manifest_identity":
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        manifest["run_id"] = "different-run"
        (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    else:
        (tmp_path / "philosophy.yaml").write_text("different: true\n", encoding="utf-8")

    with pytest.raises(TransitionIntegrityError):
        make_runner(tmp_path)

    assert {name: (tmp_path / name).read_bytes() for name in derived_names} == before


def test_manifest_created_at_is_not_part_of_stable_identity(tmp_path: Path) -> None:
    make_runner(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["created_at"] = "2024-01-02T00:00:00+00:00"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    changed = manifest_path.read_bytes()

    make_runner(tmp_path)

    assert manifest_path.read_bytes() == changed


def test_initial_cash_metadata_mismatch_precedes_public_writes(tmp_path: Path) -> None:
    make_runner(tmp_path).step(frames()[0])
    public_paths = [tmp_path / name for name in ARTIFACTS + ["events.jsonl"]]
    before = {path: path.read_bytes() for path in public_paths}
    metadata_path = tmp_path / "initial-state.json"
    metadata_before = metadata_path.read_bytes()

    with pytest.raises(TransitionIntegrityError, match="initial-state metadata mismatch"):
        ExperimentRunner(
            experiment=make_experiment(),
            run_dir=tmp_path,
            generate_target=stub_generator,
            benchmarks=BENCHMARKS,
            philosophy_yaml=PHILOSOPHY_YAML,
            initial_cash=Decimal("99999.00"),
        )

    assert {path: path.read_bytes() for path in public_paths} == before
    assert metadata_path.read_bytes() == metadata_before
    metadata = json.loads(metadata_before)
    created = EventLog(tmp_path / "events.jsonl", "run-test").read()[0]
    assert metadata == {
        "created_as_of": "2024-01-01T00:00:00+00:00",
        "initial_cash": "100000.00",
        "run_id": "run-test",
        "schema_version": 1,
    }
    assert created["payload"] == {
        "cash": metadata["initial_cash"],
        "as_of": metadata["created_as_of"],
    }


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
