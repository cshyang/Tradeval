"""Crash-safety and deterministic projection tests for transition journals."""

from __future__ import annotations

from pathlib import Path

import pytest

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
