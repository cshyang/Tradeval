"""Crash-safety and deterministic projection tests for transition journals."""

from __future__ import annotations

import json
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
    "events": [],
    "decisions": [{"rank": 1}],
    "orders": [],
    "fills": [],
    "portfolio": {"cash": "100.00"},
    "equity": {
        "date": SESSION,
        "equity": "100.00",
        "spy_equity": "101.00",
        "equal_weight_equity": "99.00",
    },
}


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

    conflicting = TRANSITION | {"fills": [{"symbol": "AAA"}]}
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
    conflicting = TRANSITION | {"fills": [{"symbol": "AAA"}]}

    errors = _commit_concurrently_at_publication(
        tmp_path, monkeypatch, [TRANSITION, conflicting]
    )

    assert sum(error is None for error in errors) == 1
    integrity_errors = [error for error in errors if error is not None]
    assert len(integrity_errors) == 1
    assert isinstance(integrity_errors[0], TransitionIntegrityError)
    store = TransitionStore(tmp_path)
    final = store.read_all()[0]
    assert final in (TRANSITION, conflicting)
    assert store.path(SESSION).read_bytes() == (
        json.dumps(final, indent=2, sort_keys=True) + "\n"
    ).encode()
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
    assert store.path(SESSION).read_bytes() == (
        json.dumps(TRANSITION, indent=2, sort_keys=True) + "\n"
    ).encode()
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
        if (
            threading.get_ident() == duplicate_thread
            and stat.S_ISDIR(transition_module.os.fstat(descriptor).st_mode)
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
    later = TRANSITION | {"session": "2024-01-15"}
    store.commit("2024-01-15", later)
    store.commit(SESSION, TRANSITION)

    assert [item["session"] for item in store.read_all()] == [SESSION, "2024-01-15"]
