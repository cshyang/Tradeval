"""Validation for prior-close/next-open simulation frames."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime

import pytest

from retailtrader.simulation.frame import SimulationFrame
from tests.helpers import close_dt, make_snapshot, open_dt

DECISION_DAY = date(2024, 1, 5)
EXECUTION_DAY = date(2024, 1, 8)
PRICES = {"AAA": ("10.00", "11.00")}


def test_frame_accepts_prior_close_next_open_and_is_frozen() -> None:
    frame = SimulationFrame(
        decision=make_snapshot(DECISION_DAY, PRICES),
        execution=make_snapshot(EXECUTION_DAY, PRICES),
        execution_at=open_dt(EXECUTION_DAY),
    )

    assert frame.execution_session == EXECUTION_DAY
    with pytest.raises(FrozenInstanceError):
        frame.execution_at = close_dt(EXECUTION_DAY)  # type: ignore[misc]


@pytest.mark.parametrize(
    "execution_at",
    [close_dt(DECISION_DAY), close_dt(EXECUTION_DAY)],
)
def test_frame_requires_execution_strictly_between_snapshot_times(
    execution_at: datetime,
) -> None:
    with pytest.raises(ValueError, match="decision.as_of < execution_at < execution.as_of"):
        SimulationFrame(
            decision=make_snapshot(DECISION_DAY, PRICES),
            execution=make_snapshot(EXECUTION_DAY, PRICES),
            execution_at=execution_at,
        )


def test_frame_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="execution_at must be timezone-aware"):
        SimulationFrame(
            decision=make_snapshot(DECISION_DAY, PRICES),
            execution=make_snapshot(EXECUTION_DAY, PRICES),
            execution_at=datetime(2024, 1, 8, 14, 30),
        )


def test_frame_rejects_naive_snapshot_timestamp_even_if_model_was_constructed() -> None:
    decision = make_snapshot(DECISION_DAY, PRICES).model_copy(
        update={"as_of": datetime(2024, 1, 5, 20)}
    )
    with pytest.raises(ValueError, match="decision.as_of must be timezone-aware"):
        SimulationFrame(
            decision=decision,
            execution=make_snapshot(EXECUTION_DAY, PRICES),
            execution_at=open_dt(EXECUTION_DAY),
        )


def test_frame_requires_every_decision_bar_before_every_execution_bar() -> None:
    decision = make_snapshot(DECISION_DAY, PRICES).model_copy(
        update={
            "as_of": datetime(2024, 1, 4, 20, tzinfo=UTC),
            "bars": make_snapshot(EXECUTION_DAY, PRICES).bars,
        }
    )
    with pytest.raises(ValueError, match="decision bar sessions"):
        SimulationFrame(
            decision=decision,
            execution=make_snapshot(EXECUTION_DAY, PRICES),
            execution_at=open_dt(EXECUTION_DAY),
        )
