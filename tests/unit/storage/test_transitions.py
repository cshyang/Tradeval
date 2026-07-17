"""Atomic publication tests for canonical simulation journals."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import retailtrader.storage.transitions as transition_module
from retailtrader.storage.transitions import TransitionIntegrityError, TransitionStore

SESSION = "2024-01-08"
TRANSITION = {
    "schema_version": 1,
    "run_id": "run-test",
    "session": SESSION,
    "reference_column": "synthetic_mega_cap_proxy_equity",
    "reference_equity": "101.00",
    "equal_weight_equity": "99.00",
    "events": [
        {
            "schema_version": 1,
            "run_id": "run-test",
            "event_type": "target_generated",
            "as_of": "2024-01-05T20:00:00+00:00",
            "created_at": "2024-01-08T20:00:01+00:00",
            "payload": {
                "target": {
                    "run_id": "run-test",
                    "as_of": "2024-01-05T20:00:00+00:00",
                    "cash_weight": 1.0,
                    "positions": [],
                },
                "decisions": [],
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
}


def make_store(run_dir: Path, **kwargs) -> TransitionStore:
    return TransitionStore(
        run_dir,
        run_id="run-test",
        schema_version=1,
        reference_column="synthetic_mega_cap_proxy_equity",
        **kwargs,
    )


def test_run_state_is_immutable_and_exact_retries_are_idempotent(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    arguments = {
        "created_as_of": datetime(2024, 1, 1, tzinfo=UTC),
        "initial_cash": Decimal("100.00"),
        "slippage_bps": 10,
        "max_turnover": 0.2,
    }

    state = store.initialize_state(**arguments)
    store.initialize_state(**arguments)

    assert store.read_state(**arguments) == state
    with pytest.raises(TransitionIntegrityError, match="initial_cash"):
        store.read_state(**(arguments | {"initial_cash": Decimal("99.00")}))


def test_journal_commit_is_immutable_and_idempotent(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    store.commit(SESSION, TRANSITION)
    before = store.path(SESSION).read_bytes()
    store.commit(SESSION, TRANSITION)

    assert store.path(SESSION).read_bytes() == before
    assert store.read_all() == [TRANSITION]
    conflicting = TRANSITION | {"reference_equity": "102.00"}
    with pytest.raises(TransitionIntegrityError, match="conflicting"):
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
def test_failure_boundaries_leave_only_atomic_journal_outcomes(
    tmp_path: Path, failure_point: str, journal_present: bool
) -> None:
    def fail(point: str) -> None:
        if point == failure_point:
            raise OSError(f"injected at {point}")

    store = make_store(tmp_path, failure_hook=fail)
    with pytest.raises(OSError, match="injected"):
        store.commit(SESSION, TRANSITION)

    assert store.path(SESSION).exists() is journal_present
    assert make_store(tmp_path).read_all() == ([TRANSITION] if journal_present else [])


def test_concurrent_conflicting_publishers_cannot_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    barrier = threading.Barrier(2)
    real_link = transition_module.os.link

    def synchronized_link(source: Path, target: Path) -> None:
        barrier.wait(timeout=5)
        real_link(source, target)

    monkeypatch.setattr(transition_module.os, "link", synchronized_link)
    conflicting = TRANSITION | {"reference_equity": "102.00"}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(make_store(tmp_path).commit, SESSION, transition)
            for transition in (TRANSITION, conflicting)
        ]
    errors = [future.exception() for future in futures]

    assert sum(error is None for error in errors) == 1
    assert sum(isinstance(error, TransitionIntegrityError) for error in errors) == 1
    final = make_store(tmp_path).read_all()[0]
    assert final in (TRANSITION, conflicting)
    assert make_store(tmp_path).path(SESSION).read_bytes() == (
        json.dumps(final, indent=2, sort_keys=True) + "\n"
    ).encode()
