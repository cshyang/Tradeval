"""Point-in-time weekly frame and reference-index invariants."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from retailtrader.data.protocol import AvailableMarketBar, PriceBatch, PriceQuery
from retailtrader.data.replay import (
    build_price_frames,
    build_reference_indices,
    history_as_of,
    market_close_utc,
    market_open_utc,
    weekly_session_pairs,
)
from retailtrader.domain import MarketBar

NY = ZoneInfo("America/New_York")
SYMBOLS = ("AAPL", "MSFT", "SPY")
SESSIONS = (
    date(2025, 1, 2),
    date(2025, 1, 3),
    date(2025, 1, 6),
    date(2025, 1, 7),
    date(2025, 1, 10),
    date(2025, 1, 13),
)


def _observation(
    symbol: str,
    session: date,
    open_price: str,
    close_price: str,
    *,
    open_delay_minutes: int = 0,
    close_delay_minutes: int = 0,
) -> AvailableMarketBar:
    open_value = Decimal(open_price)
    close_value = Decimal(close_price)
    return AvailableMarketBar(
        bar=MarketBar(
            symbol=symbol,
            session=session,
            open=open_value,
            high=max(open_value, close_value),
            low=min(open_value, close_value),
            close=close_value,
            volume=1_000_000,
        ),
        open_available_at=datetime(
            session.year, session.month, session.day, 9, 30, tzinfo=NY
        )
        + timedelta(minutes=open_delay_minutes),
        close_available_at=datetime(
            session.year, session.month, session.day, 16, tzinfo=NY
        )
        + timedelta(minutes=close_delay_minutes),
        source_ref=f"fixture:{symbol}:{session}",
    )


def _prices(session: date, symbol: str) -> tuple[str, str]:
    index = SESSIONS.index(session)
    bases = {"AAPL": 10, "MSFT": 20, "SPY": 100}
    base = bases[symbol] + index
    return str(base), str(base + 1)


def _batch(
    *,
    omit: set[tuple[str, date]] | None = None,
    override: dict[tuple[str, date], AvailableMarketBar] | None = None,
) -> PriceBatch:
    omit = omit or set()
    override = override or {}
    observations = []
    for session in SESSIONS:
        for symbol in SYMBOLS:
            if (symbol, session) in omit:
                continue
            open_price, close_price = _prices(session, symbol)
            observations.append(
                override.get(
                    (symbol, session),
                    _observation(symbol, session, open_price, close_price),
                )
            )
    query = PriceQuery(SYMBOLS, SESSIONS[0], SESSIONS[-1])
    return PriceBatch.create(
        transport="fixture",
        provider="fixture",
        query=query,
        observations=tuple(observations),
        retrieved_at=datetime(2025, 1, 14, 12, tzinfo=UTC),
        raw_hash="a" * 64,
        provider_versions=(("fixture", "1"),),
    )


def test_market_times_are_dst_aware() -> None:
    assert market_open_utc(date(2025, 1, 6)).hour == 14
    assert market_close_utc(date(2025, 1, 6)).hour == 21
    assert market_open_utc(date(2025, 7, 7)).hour == 13
    assert market_close_utc(date(2025, 7, 7)).hour == 20


def test_weekly_pairs_use_last_week_session_and_next_actual_session() -> None:
    pairs = weekly_session_pairs(SESSIONS, date(2025, 1, 6), date(2025, 1, 13))
    assert pairs == (
        (date(2025, 1, 3), date(2025, 1, 6)),
        (date(2025, 1, 10), date(2025, 1, 13)),
    )

    holiday = (
        date(2025, 1, 16),
        date(2025, 1, 17),
        date(2025, 1, 21),
    )
    assert weekly_session_pairs(holiday, date(2025, 1, 21), date(2025, 1, 21)) == (
        (date(2025, 1, 17), date(2025, 1, 21)),
    )


def test_implausible_reference_gap_fails_loudly() -> None:
    with pytest.raises(ValueError, match="implausible session gap"):
        weekly_session_pairs(
            (date(2025, 1, 2), date(2025, 1, 15)),
            date(2025, 1, 1),
            date(2025, 1, 31),
        )


def test_frames_exclude_spy_from_strategy_and_separate_decision_execution() -> None:
    frames = build_price_frames(
        _batch(), ("AAPL", "MSFT"), date(2025, 1, 6), date(2025, 1, 13)
    )

    assert len(frames) == 2
    first = frames[0]
    assert first.decision.as_of == datetime(2025, 1, 3, 21, tzinfo=UTC)
    assert first.execution_at == datetime(2025, 1, 6, 14, 30, tzinfo=UTC)
    assert first.execution.as_of == datetime(2025, 1, 6, 21, tzinfo=UTC)
    assert {bar.symbol for bar in first.decision.bars} == {"AAPL", "MSFT"}
    assert {bar.symbol for bar in first.execution.bars} == {"AAPL", "MSFT"}
    assert {bar.session for bar in first.decision.bars} == {date(2025, 1, 3)}
    assert {bar.session for bar in first.execution.bars} == {date(2025, 1, 6)}


def test_unavailable_decision_or_execution_data_is_rejected() -> None:
    late_close = _observation(
        "AAPL", date(2025, 1, 3), "11", "12", close_delay_minutes=30
    )
    with pytest.raises(ValueError, match="decision bar unavailable"):
        build_price_frames(
            _batch(override={("AAPL", date(2025, 1, 3)): late_close}),
            ("AAPL", "MSFT"),
            date(2025, 1, 6),
            date(2025, 1, 6),
        )

    late_open = _observation(
        "AAPL", date(2025, 1, 6), "12", "13", open_delay_minutes=30
    )
    with pytest.raises(ValueError, match="execution open unavailable"):
        build_price_frames(
            _batch(override={("AAPL", date(2025, 1, 6)): late_open}),
            ("AAPL", "MSFT"),
            date(2025, 1, 6),
            date(2025, 1, 6),
        )


def test_missing_symbol_bar_fails_without_shrinking_universe() -> None:
    with pytest.raises(ValueError, match="missing execution bars.*AAPL"):
        build_price_frames(
            _batch(omit={("AAPL", date(2025, 1, 6))}),
            ("AAPL", "MSFT"),
            date(2025, 1, 6),
            date(2025, 1, 6),
        )


def test_history_is_gated_by_session_and_close_availability() -> None:
    batch = _batch()
    decision_at = market_close_utc(date(2025, 1, 3))

    history = history_as_of(batch, ("AAPL", "MSFT"), decision_at)

    assert {bar.session for bar in history["AAPL"]} == {
        date(2025, 1, 2),
        date(2025, 1, 3),
    }
    assert all(bar.symbol != "SPY" for bars in history.values() for bar in bars)
    assert date(2025, 1, 6) not in {bar.session for bar in history["AAPL"]}


def test_reference_indices_reject_unavailable_benchmark_open() -> None:
    execution = date(2025, 1, 6)
    late_spy = _observation(
        "SPY", execution, "100", "101", open_delay_minutes=30
    )
    batch = _batch(override={("SPY", execution): late_spy})
    frames = build_price_frames(
        batch, ("AAPL", "MSFT"), execution, execution
    )

    with pytest.raises(ValueError, match="reference open unavailable"):
        build_reference_indices(
            frames, batch, ("AAPL", "MSFT"), Decimal("100000")
        )


def test_reference_indices_start_at_first_execution_open() -> None:
    first_execution = date(2025, 1, 6)
    second_execution = date(2025, 1, 13)
    overrides = {
        ("SPY", first_execution): _observation("SPY", first_execution, "100", "110"),
        ("AAPL", first_execution): _observation("AAPL", first_execution, "10", "11"),
        ("MSFT", first_execution): _observation("MSFT", first_execution, "20", "18"),
        ("SPY", second_execution): _observation("SPY", second_execution, "95", "90"),
        ("AAPL", second_execution): _observation("AAPL", second_execution, "11", "12"),
        ("MSFT", second_execution): _observation("MSFT", second_execution, "19", "22"),
    }
    batch = _batch(override=overrides)
    frames = build_price_frames(
        batch, ("AAPL", "MSFT"), first_execution, second_execution
    )

    references = build_reference_indices(
        frames, batch, ("AAPL", "MSFT"), Decimal("100000")
    )

    assert references[first_execution] == (Decimal("110000.00"), Decimal("100000.00"))
    assert references[second_execution] == (Decimal("90000.00"), Decimal("115000.00"))
