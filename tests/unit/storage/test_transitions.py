"""Crash-safety and deterministic projection tests for transition journals."""

from __future__ import annotations

import json
import multiprocessing
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import retailtrader.storage.transitions as transition_module
from retailtrader.storage.transitions import TransitionIntegrityError, TransitionStore


SESSION = "2024-01-08"
TRANSITION = {
    "schema_version": 1,
    "run_id": "run-test",
    "session": SESSION,
    "target": {
        "run_id": "run-test",
        "as_of": "2024-01-05T20:00:00+00:00",
        "cash_weight": 1.0,
        "positions": [],
    },
    "events": [
        {
            "schema_version": 1,
            "run_id": "run-test",
            "event_type": "target_generated",
            "as_of": "2024-01-05T20:00:00+00:00",
            "created_at": "2024-01-08T20:00:01+00:00",
            "payload": {
                "run_id": "run-test",
                "as_of": "2024-01-05T20:00:00+00:00",
                "cash_weight": 1.0,
                "positions": [],
            },
        },
        {
            "schema_version": 1,
            "run_id": "run-test",
            "event_type": "portfolio_marked",
            "as_of": "2024-01-08T20:00:00+00:00",
            "created_at": "2024-01-08T20:00:01+00:00",
            "payload": {
                "as_of": "2024-01-08T20:00:00+00:00",
                "cash": "100.00",
                "positions": [],
                "total_equity": "100.00",
            },
        },
        {
            "schema_version": 1,
            "run_id": "run-test",
            "event_type": "rebalance_completed",
            "as_of": "2024-01-08T20:00:00+00:00",
            "created_at": "2024-01-08T20:00:01+00:00",
            "payload": {"session": SESSION},
        },
    ],
    "decisions": [],
    "orders": [],
    "rejections": [],
    "fills": [],
    "portfolio": {
        "as_of": "2024-01-08T20:00:00+00:00",
        "cash": "100.00",
        "positions": [],
        "total_equity": "100.00",
    },
    "references": {"spy_equity": "101.00", "equal_weight_equity": "99.00"},
    "equity": {
        "date": SESSION,
        "equity": "100.00",
        "spy_equity": "101.00",
        "equal_weight_equity": "99.00",
    },
}


def _conflicting_transition() -> dict[str, object]:
    return TRANSITION | {
        "events": [
            *TRANSITION["events"][:-1],
            TRANSITION["events"][-1] | {"created_at": "2024-01-08T20:00:02+00:00"},
        ]
    }


def _hold_run_lock(
    run_dir: Path,
    entered: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    with TransitionStore(run_dir).locked():
        entered.set()
        if not release.wait(timeout=5):
            raise TimeoutError("run lock was not released")


def test_run_lock_serializes_distinct_processes(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    first_entered = context.Event()
    release_first = context.Event()
    second_entered = context.Event()
    release_second = context.Event()
    first = context.Process(target=_hold_run_lock, args=(tmp_path, first_entered, release_first))
    second = context.Process(target=_hold_run_lock, args=(tmp_path, second_entered, release_second))
    first.start()
    try:
        assert first_entered.wait(timeout=5)
        second.start()
        assert not second_entered.wait(timeout=0.2)
        release_first.set()
        assert second_entered.wait(timeout=5)
        release_second.set()
        second.join(timeout=5)
        assert second.exitcode == 0
    finally:
        release_first.set()
        release_second.set()
        first.join(timeout=5)
        if second.pid is not None:
            second.join(timeout=5)
    assert first.exitcode == 0


def test_first_commit_fsyncs_run_directory_and_transition_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fsynced_inodes: list[int] = []
    real_fsync = transition_module.os.fsync

    def recording_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(transition_module.os.fstat(descriptor).st_mode):
            fsynced_inodes.append(transition_module.os.fstat(descriptor).st_ino)
        real_fsync(descriptor)

    monkeypatch.setattr(transition_module.os, "fsync", recording_fsync)
    store = TransitionStore(tmp_path)
    store.commit(SESSION, TRANSITION)

    assert tmp_path.stat().st_ino in fsynced_inodes
    assert store.directory.stat().st_ino in fsynced_inodes


def test_commit_is_durable_and_exact_content_is_idempotent(tmp_path: Path) -> None:
    store = TransitionStore(tmp_path)

    store.commit(SESSION, TRANSITION)
    before = store.path(SESSION).read_bytes()
    store.commit(SESSION, TRANSITION)

    assert store.path(SESSION).read_bytes() == before
    assert store.read_all() == [TRANSITION]


def test_conflicting_content_for_session_raises_integrity_error(tmp_path: Path) -> None:
    store = TransitionStore(tmp_path)
    store.commit(SESSION, TRANSITION)

    conflicting = _conflicting_transition()
    with pytest.raises(TransitionIntegrityError, match=SESSION):
        store.commit(SESSION, conflicting)


def _commit_concurrently_at_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transitions: list[dict[str, object]],
) -> list[BaseException | None]:
    publication_barrier = threading.Barrier(len(transitions))
    real_link = transition_module.os.link

    def synchronized_link(source: Path, target: Path) -> None:
        publication_barrier.wait(timeout=5)
        real_link(source, target)

    monkeypatch.setattr(transition_module.os, "link", synchronized_link)
    with ThreadPoolExecutor(max_workers=len(transitions)) as executor:
        futures = [
            executor.submit(TransitionStore(tmp_path).commit, SESSION, transition)
            for transition in transitions
        ]
    return [future.exception() for future in futures]


def test_concurrent_conflicting_writers_cannot_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conflicting = _conflicting_transition()

    errors = _commit_concurrently_at_publication(tmp_path, monkeypatch, [TRANSITION, conflicting])

    assert sum(error is None for error in errors) == 1
    integrity_errors = [error for error in errors if error is not None]
    assert len(integrity_errors) == 1
    assert isinstance(integrity_errors[0], TransitionIntegrityError)
    store = TransitionStore(tmp_path)
    final = store.read_all()[0]
    assert final in (TRANSITION, conflicting)
    assert (
        store.path(SESSION).read_bytes()
        == (json.dumps(final, indent=2, sort_keys=True) + "\n").encode()
    )
    assert list(store.directory.glob(f".{SESSION}.*")) == []


def test_concurrent_exact_duplicate_writers_are_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors = _commit_concurrently_at_publication(
        tmp_path, monkeypatch, [TRANSITION, TRANSITION.copy()]
    )

    assert errors == [None, None]
    store = TransitionStore(tmp_path)
    assert store.read_all() == [TRANSITION]
    assert (
        store.path(SESSION).read_bytes()
        == (json.dumps(TRANSITION, indent=2, sort_keys=True) + "\n").encode()
    )
    assert list(store.directory.glob(f".{SESSION}.*")) == []


def test_exact_duplicate_fsyncs_directory_while_publisher_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = threading.Event()
    release_publisher = threading.Event()
    duplicate_fsynced_directory = threading.Event()
    duplicate_thread = threading.get_ident()
    real_fsync = transition_module.os.fsync

    def recording_fsync(descriptor: int) -> None:
        if threading.get_ident() == duplicate_thread and stat.S_ISDIR(
            transition_module.os.fstat(descriptor).st_mode
        ):
            duplicate_fsynced_directory.set()
        real_fsync(descriptor)

    def block_after_publication(point: str) -> None:
        if point == "after_journal_replace":
            published.set()
            if not release_publisher.wait(timeout=5):
                raise TimeoutError("publisher was not released")

    monkeypatch.setattr(transition_module.os, "fsync", recording_fsync)
    publisher_store = TransitionStore(tmp_path, failure_hook=block_after_publication)
    with ThreadPoolExecutor(max_workers=1) as executor:
        publisher = executor.submit(publisher_store.commit, SESSION, TRANSITION)
        try:
            assert published.wait(timeout=5)
            assert not publisher.done()

            TransitionStore(tmp_path).commit(SESSION, TRANSITION.copy())

            assert duplicate_fsynced_directory.is_set()
            assert not publisher.done()
        finally:
            release_publisher.set()
        publisher.result(timeout=5)


@pytest.mark.parametrize(
    ("failure_point", "journal_present"),
    [
        ("before_journal_replace", False),
        ("after_journal_replace", True),
        ("before_parent_fsync", True),
        ("after_parent_fsync", True),
    ],
)
def test_commit_failure_boundaries_leave_only_atomic_outcomes(
    tmp_path: Path, failure_point: str, journal_present: bool
) -> None:
    def fail(point: str) -> None:
        if point == failure_point:
            raise OSError(f"injected at {point}")

    store = TransitionStore(tmp_path, failure_hook=fail)
    with pytest.raises(OSError, match="injected"):
        store.commit(SESSION, TRANSITION)

    assert store.path(SESSION).exists() is journal_present
    recovered = TransitionStore(tmp_path)
    assert recovered.read_all() == ([TRANSITION] if journal_present else [])
    if journal_present:
        recovered.commit(SESSION, TRANSITION)
        assert recovered.read_all() == [TRANSITION]


def test_journals_are_read_in_session_order(tmp_path: Path) -> None:
    store = TransitionStore(tmp_path)
    later_session = "2024-01-15"
    later = TRANSITION | {
        "session": later_session,
        "portfolio": TRANSITION["portfolio"] | {"as_of": "2024-01-15T20:00:00+00:00"},
        "equity": TRANSITION["equity"] | {"date": later_session},
        "events": [
            TRANSITION["events"][0],
            TRANSITION["events"][1]
            | {
                "as_of": "2024-01-15T20:00:00+00:00",
                "payload": TRANSITION["portfolio"] | {"as_of": "2024-01-15T20:00:00+00:00"},
            },
            TRANSITION["events"][2]
            | {
                "as_of": "2024-01-15T20:00:00+00:00",
                "payload": {"session": later_session},
            },
        ],
    }
    store.commit(later_session, later)
    store.commit(SESSION, TRANSITION)

    assert [item["session"] for item in store.read_all()] == [SESSION, "2024-01-15"]
