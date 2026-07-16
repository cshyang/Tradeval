"""End-to-end stub-generator replay: replay-vs-forward parity, idempotency,
ledger reconstruction, and artifact shapes matching tests/fixtures/demo-run."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from retailtrader.domain import ExperimentManifest
from retailtrader.evaluation.metrics import compute_evaluation, read_equity_csv
from retailtrader.evaluation.report import write_evaluation_json
from retailtrader.simulation.ledger import replay_events
from retailtrader.simulation.runner import (
    ExperimentRunner,
    ResumeMismatchError,
    remaining_session_suffix,
)
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


def make_runner(
    run_dir: Path,
    *,
    experiment: ExperimentManifest | None = None,
    philosophy_yaml: str = PHILOSOPHY_YAML,
    max_turnover: float | None = 0.10,
) -> ExperimentRunner:
    return ExperimentRunner(
        experiment=experiment or make_experiment(),
        run_dir=run_dir,
        generate_target=stub_generator,
        benchmarks=BENCHMARKS,
        philosophy_yaml=philosophy_yaml,
        max_turnover=max_turnover,
    )


def events_without_created_at(run_dir: Path) -> list[str]:
    events = EventLog(run_dir / "events.jsonl", "run-test").read()
    return [
        json.dumps({key: value for key, value in event.items() if key != "created_at"})
        for event in events
    ]


def artifact_bytes(run_dir: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(run_dir)): path.read_bytes()
        for path in sorted(run_dir.iterdir())
        if path.is_file()
    }


def assert_resume_rejected_without_writes(run_dir: Path, match: str) -> None:
    before = artifact_bytes(run_dir)
    with pytest.raises(ResumeMismatchError, match=match):
        make_runner(run_dir)
    assert artifact_bytes(run_dir) == before


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
    assert any(
        order["reason"] == "max turnover" for order in read_jsonl(replay_dir / "orders.jsonl")
    )


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "other-id"),
        ("run_id", "other-run"),
        ("schema_version", 2),
        ("philosophy_name", "other"),
        ("philosophy_version", "v2"),
        ("philosophy_hash", "other-hash"),
        ("universe_hash", "other-universe"),
        ("engine_version", "9.9.9"),
        ("cadence", "monthly"),
        ("start", date(2023, 12, 1)),
        ("end", date(2024, 3, 1)),
        ("data_source", "other-source"),
        ("benchmark_source", "other-benchmark"),
        ("initial_cash", Decimal("90000.00")),
        ("slippage_bps", 25),
    ],
)
def test_resume_rejects_changed_immutable_manifest_without_writes(
    tmp_path: Path, field: str, value
) -> None:
    make_runner(tmp_path).step(snapshots()[0])
    before = artifact_bytes(tmp_path)
    changed = make_experiment().model_copy(update={field: value})

    with pytest.raises(ResumeMismatchError, match=field):
        make_runner(tmp_path, experiment=changed)

    assert artifact_bytes(tmp_path) == before


def test_resume_accepts_changed_created_at_and_adopts_persisted_manifest(tmp_path: Path) -> None:
    original = make_experiment()
    make_runner(tmp_path, experiment=original).step(snapshots()[0])
    before = artifact_bytes(tmp_path)
    incoming = original.model_copy(update={"created_at": datetime(2025, 1, 1, tzinfo=UTC)})

    resumed = make_runner(tmp_path, experiment=incoming)

    assert resumed.experiment == original
    assert artifact_bytes(tmp_path) == before


def test_resume_rejects_changed_philosophy_text_without_writes(tmp_path: Path) -> None:
    make_runner(tmp_path).step(snapshots()[0])
    before = artifact_bytes(tmp_path)
    with pytest.raises(ResumeMismatchError, match="philosophy.yaml"):
        make_runner(tmp_path, philosophy_yaml=PHILOSOPHY_YAML + "max_turnover: 0.1\n")
    assert artifact_bytes(tmp_path) == before


def test_resume_rejects_changed_turnover_setting_without_writes(tmp_path: Path) -> None:
    make_runner(tmp_path, max_turnover=0.10).step(snapshots()[0])
    before = artifact_bytes(tmp_path)
    with pytest.raises(ResumeMismatchError, match="max_turnover"):
        make_runner(tmp_path, max_turnover=0.20)
    assert artifact_bytes(tmp_path) == before


@pytest.mark.parametrize("missing", ["manifest.json", "philosophy.yaml", "events.jsonl"])
def test_resume_rejects_missing_identity_artifacts_without_writes(
    tmp_path: Path, missing: str
) -> None:
    make_runner(tmp_path).step(snapshots()[0])
    (tmp_path / missing).unlink()
    before = artifact_bytes(tmp_path)
    with pytest.raises(ResumeMismatchError, match=missing):
        make_runner(tmp_path)
    assert artifact_bytes(tmp_path) == before


@pytest.mark.parametrize(
    ("name", "contents"),
    [("manifest.json", "{"), ("events.jsonl", "not-json\n")],
)
def test_resume_rejects_malformed_identity_artifacts_without_writes(
    tmp_path: Path, name: str, contents: str
) -> None:
    make_runner(tmp_path).step(snapshots()[0])
    (tmp_path / name).write_text(contents)
    before = artifact_bytes(tmp_path)
    with pytest.raises(ResumeMismatchError, match=name):
        make_runner(tmp_path)
    assert artifact_bytes(tmp_path) == before


def test_resume_rejects_mixed_event_run_ids_without_writes(tmp_path: Path) -> None:
    make_runner(tmp_path).step(snapshots()[0])
    path = tmp_path / "events.jsonl"
    events = read_jsonl(path)
    events[1]["run_id"] = "other-run"
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    before = artifact_bytes(tmp_path)

    with pytest.raises(ResumeMismatchError, match="run_id"):
        make_runner(tmp_path)

    assert artifact_bytes(tmp_path) == before


def test_resume_rejects_mixed_event_schema_versions_without_writes(tmp_path: Path) -> None:
    make_runner(tmp_path).step(snapshots()[0])
    path = tmp_path / "events.jsonl"
    events = read_jsonl(path)
    events[-1]["schema_version"] = 2
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    before = artifact_bytes(tmp_path)

    with pytest.raises(ResumeMismatchError, match="schema_version"):
        make_runner(tmp_path)

    assert artifact_bytes(tmp_path) == before


def test_resume_restores_initial_cash_and_as_of_from_portfolio_created(tmp_path: Path) -> None:
    original = make_experiment().model_copy(update={"initial_cash": Decimal("12345.67")})
    first = make_runner(tmp_path, experiment=original)
    created = first.event_log.read()[0]["payload"]

    resumed = make_runner(tmp_path, experiment=original)

    assert resumed.portfolio.cash == Decimal(created["cash"])
    assert resumed.portfolio.total_equity == Decimal(created["cash"])
    assert resumed.portfolio.as_of == datetime.fromisoformat(created["as_of"])


def test_resume_rejects_portfolio_created_cash_mismatch(tmp_path: Path) -> None:
    make_runner(tmp_path)
    path = tmp_path / "events.jsonl"
    events = read_jsonl(path)
    events[0]["payload"]["cash"] = "1.00"
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    before = artifact_bytes(tmp_path)

    with pytest.raises(ResumeMismatchError, match="initial_cash"):
        make_runner(tmp_path)

    assert artifact_bytes(tmp_path) == before


def test_existing_partial_run_is_rejected_without_writes(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{}\n")
    before = artifact_bytes(tmp_path)
    with pytest.raises(ResumeMismatchError, match="philosophy.yaml"):
        make_runner(tmp_path)
    assert artifact_bytes(tmp_path) == before


def test_resume_rejects_portfolio_mark_without_terminal_event_or_duplicate_rows(
    tmp_path: Path,
) -> None:
    make_runner(tmp_path).step(snapshots()[0])
    events_path = tmp_path / "events.jsonl"
    events = read_jsonl(events_path)
    assert events[-2]["event_type"] == "portfolio_marked"
    assert events[-1]["event_type"] == "rebalance_completed"
    events_path.write_text("".join(json.dumps(event) + "\n" for event in events[:-1]))
    row_counts = {
        name: len((tmp_path / name).read_text().splitlines())
        for name in ("decisions.jsonl", "orders.jsonl", "fills.jsonl", "portfolio.jsonl", "equity.csv")
    }

    assert_resume_rejected_without_writes(tmp_path, "incomplete session")

    assert {
        name: len((tmp_path / name).read_text().splitlines()) for name in row_counts
    } == row_counts


def test_resume_rejects_transition_events_after_last_completed_session(tmp_path: Path) -> None:
    runner = make_runner(tmp_path)
    runner.step(snapshots()[0])
    next_snapshot = snapshots()[1]
    runner.event_log.append(
        "target_generated",
        next_snapshot.as_of,
        {
            "target": {
                "run_id": "run-test",
                "as_of": next_snapshot.as_of,
                "cash_weight": 1.0,
                "positions": [],
            },
            "decisions": [],
        },
    )
    assert_resume_rejected_without_writes(tmp_path, "incomplete session")


@pytest.mark.parametrize(
    "name",
    ["decisions.jsonl", "orders.jsonl", "fills.jsonl", "portfolio.jsonl", "equity.csv"],
)
def test_resume_rejects_missing_materialized_artifact_without_writes(
    tmp_path: Path, name: str
) -> None:
    make_runner(tmp_path).replay(snapshots()[:2])
    (tmp_path / name).unlink()
    assert_resume_rejected_without_writes(tmp_path, name)


@pytest.mark.parametrize(
    ("name", "contents"),
    [
        ("decisions.jsonl", "{\n"),
        ("orders.jsonl", "{\n"),
        ("fills.jsonl", "{\n"),
        ("portfolio.jsonl", "{\n"),
        ("equity.csv", "not,the,required,header\n"),
    ],
)
def test_resume_rejects_malformed_materialized_artifact_without_writes(
    tmp_path: Path, name: str, contents: str
) -> None:
    make_runner(tmp_path).replay(snapshots()[:2])
    (tmp_path / name).write_text(contents)
    assert_resume_rejected_without_writes(tmp_path, name)


@pytest.mark.parametrize(
    "name",
    ["decisions.jsonl", "orders.jsonl", "fills.jsonl", "portfolio.jsonl", "equity.csv"],
)
def test_resume_rejects_inconsistent_materialized_rows_without_writes(
    tmp_path: Path, name: str
) -> None:
    make_runner(tmp_path).replay(snapshots()[:2])
    path = tmp_path / name
    if name == "equity.csv":
        lines = path.read_text().splitlines()
        fields = lines[-1].split(",")
        fields[0] = "2099-01-01"
        path.write_text("\n".join([*lines[:-1], ",".join(fields)]) + "\n")
    else:
        rows = read_jsonl(path)
        if name == "decisions.jsonl":
            rows.pop()
        elif name == "orders.jsonl":
            rows.append(rows[-1])
        elif name == "fills.jsonl":
            rows[0]["quantity"] += 1
        else:
            rows[-1]["total_equity"] = "1.00"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert_resume_rejected_without_writes(tmp_path, name)


def test_completed_sessions_must_be_a_chronological_prefix() -> None:
    assert remaining_session_suffix(SESSIONS[:2], SESSIONS) == (SESSIONS[2],)
    assert remaining_session_suffix(SESSIONS, SESSIONS) == ()
    with pytest.raises(ResumeMismatchError, match="chronological prefix"):
        remaining_session_suffix((SESSIONS[1],), SESSIONS)
    with pytest.raises(ResumeMismatchError, match="chronological prefix"):
        remaining_session_suffix((SESSIONS[0], SESSIONS[2]), SESSIONS)
    with pytest.raises(ResumeMismatchError, match="chronological and unique"):
        remaining_session_suffix((), tuple(reversed(SESSIONS)))
    with pytest.raises(ResumeMismatchError, match="chronological and unique"):
        remaining_session_suffix((), (SESSIONS[0], SESSIONS[0]))
