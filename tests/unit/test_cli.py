from __future__ import annotations

from datetime import UTC, date, time
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from retailtrader.cli import INITIAL_CASH, SPY_PROXY, _benchmarks, _simulation_frames, app

from tests.helpers import make_frame


def test_demo_rejects_too_few_frames_before_writing_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path / "too-short"

    result = CliRunner().invoke(
        app,
        [
            "demo",
            "--workspace",
            str(workspace),
            "--start",
            "2024-01-05",
            "--end",
            "2024-01-12",
        ],
    )

    assert result.exit_code == 2
    assert "evaluation requires at least 3 simulation frames; got 2" in result.output
    assert not workspace.exists()


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


def test_benchmarks_fund_references_at_first_execution_open_after_overnight_gap() -> None:
    symbols = (*SPY_PROXY, "XOM")
    prior_close = {symbol: ("100", "100") for symbol in symbols}
    first_execution = {
        "AAPL": ("120", "132"),
        "MSFT": ("80", "72"),
        "NVDA": ("200", "220"),
        "AMZN": ("50", "50"),
        "GOOGL": ("40", "42"),
        "XOM": ("125", "100"),
    }
    second_execution = {
        "AAPL": ("140", "144"),
        "MSFT": ("90", "88"),
        "NVDA": ("190", "180"),
        "AMZN": ("54", "55"),
        "GOOGL": ("39", "38"),
        "XOM": ("130", "137.50"),
    }
    frames = [
        make_frame(date(2024, 1, 5), date(2024, 1, 8), prior_close, first_execution),
        make_frame(date(2024, 1, 12), date(2024, 1, 16), first_execution, second_execution),
    ]

    references = _benchmarks([frame.execution for frame in frames], symbols)

    assert references == {
        date(2024, 1, 8): (Decimal("103000.00"), Decimal("99166.67")),
        date(2024, 1, 16): (Decimal("105000.00"), Decimal("105833.33")),
    }
    assert all(reference != INITIAL_CASH for reference in references[date(2024, 1, 8)])
