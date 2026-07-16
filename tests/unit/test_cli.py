from __future__ import annotations

from datetime import UTC, date, time

from retailtrader.cli import _simulation_frames


def test_simulation_frames_execute_on_next_session_after_decision() -> None:
    frames = _simulation_frames(
        ("AAPL",),
        [date(2024, 1, 5), date(2024, 7, 5)],
    )

    assert [frame.decision.as_of.date() for frame in frames] == [
        date(2024, 1, 5),
        date(2024, 7, 5),
    ]
    assert [frame.execution.as_of.date() for frame in frames] == [
        date(2024, 1, 8),
        date(2024, 7, 8),
    ]
    assert [frame.execution_at.astimezone(UTC).time() for frame in frames] == [
        time(14, 30),
        time(13, 30),
    ]
    for frame in frames:
        assert frame.decision is not frame.execution
        assert frame.decision.bars[0].session < frame.execution.bars[0].session
        assert frame.decision.as_of < frame.execution_at < frame.execution.as_of
