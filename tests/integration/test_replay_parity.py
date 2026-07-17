"""End-to-end stub-generator replay: replay-vs-forward parity, idempotency,
ledger reconstruction, and artifact shapes matching tests/fixtures/demo-run."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
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
from retailtrader.storage.transitions import FailureHook
from tests.helpers import make_experiment, make_frame, stub_generator

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
PROJECTIONS = [
    "events.jsonl",
    "decisions.jsonl",
    "orders.jsonl",
    "fills.jsonl",
    "portfolio.jsonl",
    "equity.csv",
]


def snapshots() -> list:
    return [
        make_frame(
            session - timedelta(days=3),
            session,
            PRICES[session],
            PRICES[session],
        )
        for session in SESSIONS
    ]


def make_runner(
    run_dir: Path,
    *,
    experiment: ExperimentManifest | None = None,
    philosophy_yaml: str = PHILOSOPHY_YAML,
    max_turnover: float | None = 0.10,
    data_provenance: dict[str, object] | None = None,
    generate_target=stub_generator,
    failure_hook: FailureHook | None = None,
) -> ExperimentRunner:
    return ExperimentRunner(
        experiment=experiment or make_experiment(),
        run_dir=run_dir,
        generate_target=generate_target,
        benchmarks=BENCHMARKS,
        philosophy_yaml=philosophy_yaml,
        max_turnover=max_turnover,
        data_provenance=data_provenance,
        failure_hook=failure_hook,
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


def test_committed_journal_recovers_combined_timing_turnover_and_identity_contracts(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "recovered"
    frame = snapshots()[0]
    received = []

    def recording_generator(experiment, snapshot):
        received.append(snapshot)
        return stub_generator(experiment, snapshot)

    make_runner(run_dir, generate_target=recording_generator).step(frame)

    assert received == [frame.decision]
    events = EventLog(run_dir / "events.jsonl", "run-test").read()
    execution_events = [
        event
        for event in events
        if event["event_type"] in {"order_created", "order_rejected", "order_filled"}
    ]
    assert execution_events
    assert {event["as_of"] for event in execution_events} == {
        frame.execution_at.isoformat()
    }
    assert any(
        event["event_type"] == "order_rejected"
        and event["payload"]["reason"] == "max turnover"
        for event in execution_events
    )

    journal = run_dir / f"transitions/{frame.execution_session.isoformat()}.json"
    assert journal.is_file()
    expected = {name: (run_dir / name).read_bytes() for name in ARTIFACTS + ["events.jsonl"]}
    (run_dir / "orders.jsonl").write_text("", encoding="utf-8")

    make_runner(run_dir, generate_target=recording_generator)

    assert received == [frame.decision]
    assert {
        name: (run_dir / name).read_bytes() for name in ARTIFACTS + ["events.jsonl"]
    } == expected

    before = artifact_bytes(run_dir)
    changed = make_experiment().model_copy(update={"universe_hash": "changed"})
    with pytest.raises(ResumeMismatchError, match="universe_hash"):
        make_runner(run_dir, experiment=changed)
    assert artifact_bytes(run_dir) == before


def test_restart_recovers_after_committed_journal_projection_failure(tmp_path: Path) -> None:
    failed = False

    def fail_once(point: str) -> None:
        nonlocal failed
        if point == "after_artifact_replace:orders.jsonl" and not failed:
            failed = True
            raise OSError("injected after orders projection")

    run_dir = tmp_path / "interrupted"
    with pytest.raises(OSError, match="injected"):
        make_runner(run_dir, failure_hook=fail_once).step(snapshots()[0])

    assert len(list((run_dir / "transitions").glob("*.json"))) == 1
    make_runner(run_dir)
    clean_dir = tmp_path / "clean"
    make_runner(clean_dir).step(snapshots()[0])

    for name in PROJECTIONS:
        if name == "events.jsonl":
            continue
        assert (run_dir / name).read_bytes() == (clean_dir / name).read_bytes(), name
    assert events_without_created_at(run_dir) == events_without_created_at(clean_dir)


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


def test_resume_rejects_changed_provenance_without_writes(tmp_path: Path) -> None:
    provenance = {"kind": "synthetic", "normalized_hash": "a" * 64}
    make_runner(tmp_path, data_provenance=provenance).step(snapshots()[0])
    before = artifact_bytes(tmp_path)

    with pytest.raises(ResumeMismatchError, match="data-provenance.json mismatch"):
        make_runner(
            tmp_path,
            data_provenance=provenance | {"normalized_hash": "b" * 64},
        )

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


@pytest.mark.parametrize("missing", ["manifest.json", "philosophy.yaml", "run-state.json"])
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
    [("manifest.json", "{"), ("run-state.json", "not-json\n")],
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


def test_resume_restores_initial_cash_and_as_of_from_portfolio_created(tmp_path: Path) -> None:
    original = make_experiment().model_copy(update={"initial_cash": Decimal("12345.67")})
    first = make_runner(tmp_path, experiment=original)
    created = first.event_log.read()[0]["payload"]

    resumed = make_runner(tmp_path, experiment=original)

    assert resumed.portfolio.cash == Decimal(created["cash"])
    assert resumed.portfolio.total_equity == Decimal(created["cash"])
    assert resumed.portfolio.as_of == datetime.fromisoformat(created["as_of"])


def test_resume_rejects_run_state_cash_mismatch(tmp_path: Path) -> None:
    make_runner(tmp_path).step(snapshots()[0])
    path = tmp_path / "run-state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["initial_cash"] = "1.00"
    path.write_text(json.dumps(state), encoding="utf-8")
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


@pytest.mark.parametrize("name", PROJECTIONS)
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_resume_rematerializes_public_projections_from_committed_journals(
    tmp_path: Path, name: str, damage: str
) -> None:
    make_runner(tmp_path).replay(snapshots()[:2])
    expected = {projection: (tmp_path / projection).read_bytes() for projection in PROJECTIONS}
    path = tmp_path / name
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("corrupt\n", encoding="utf-8")

    make_runner(tmp_path)

    assert {projection: (tmp_path / projection).read_bytes() for projection in PROJECTIONS} == expected


def test_resume_rejects_corrupt_canonical_journal_without_projection_writes(
    tmp_path: Path,
) -> None:
    make_runner(tmp_path).step(snapshots()[0])
    journal_path = next((tmp_path / "transitions").glob("*.json"))
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["events"][0]["run_id"] = "other-run"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    before = {projection: (tmp_path / projection).read_bytes() for projection in PROJECTIONS}

    with pytest.raises(ResumeMismatchError, match="run_id"):
        make_runner(tmp_path)

    assert {projection: (tmp_path / projection).read_bytes() for projection in PROJECTIONS} == before


def test_resume_rejects_journal_order_fill_mismatch_without_projection_writes(
    tmp_path: Path,
) -> None:
    make_runner(tmp_path).step(snapshots()[0])
    journal_path = next((tmp_path / "transitions").glob("*.json"))
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    created = next(
        event for event in journal["events"] if event["event_type"] == "order_created"
    )
    created["payload"]["quantity"] += 1
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    before = {projection: (tmp_path / projection).read_bytes() for projection in PROJECTIONS}

    with pytest.raises(ResumeMismatchError, match="bijection"):
        make_runner(tmp_path)

    assert {projection: (tmp_path / projection).read_bytes() for projection in PROJECTIONS} == before


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
