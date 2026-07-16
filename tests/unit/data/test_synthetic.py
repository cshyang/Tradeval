"""Invariant tests for the deterministic synthetic provider."""

from datetime import UTC, date, datetime, timedelta

from retailtrader.data.synthetic import (
    AVAILABILITY_LAG_DAYS,
    FUNDAMENTAL_METRICS,
    fundamentals,
    price_history,
    snapshot_for,
    trading_sessions,
)

AS_OF = datetime(2026, 7, 1, 20, tzinfo=UTC)


def test_series_is_deterministic():
    a = price_history("AAPL", AS_OF)
    b = price_history("AAPL", AS_OF)
    assert a == b
    assert price_history("MSFT", AS_OF) != a


def test_history_excludes_execution_bar():
    bars = price_history("AAPL", AS_OF)
    assert bars
    assert all(b.session < AS_OF.date() for b in bars)
    assert len(bars) > 253  # enough lookback for momentum_12m


def test_fundamentals_respect_availability_lag():
    obs = fundamentals("AAPL", AS_OF)
    assert obs
    for o in obs:
        assert o.available_at <= AS_OF
        assert o.available_at.date() == o.period_end + timedelta(days=AVAILABILITY_LAG_DAYS)
        assert o.availability_source == "approximated"
    assert {o.metric for o in obs} == set(FUNDAMENTAL_METRICS)


def test_snapshot_builds_for_session():
    session = trading_sessions(date(2026, 6, 1), date(2026, 6, 30))[0]
    snap = snapshot_for(("AAPL", "MSFT", "NVDA"), session)
    assert {b.symbol for b in snap.bars} == {"AAPL", "MSFT", "NVDA"}
    assert snap.fundamentals
    assert snap.as_of.date() == session


def test_snapshot_uses_new_york_market_close():
    january = snapshot_for(("AAPL",), date(2026, 1, 2))
    july = snapshot_for(("AAPL",), date(2026, 7, 1))

    assert january.as_of == datetime(2026, 1, 2, 21, tzinfo=UTC)
    assert july.as_of == datetime(2026, 7, 1, 20, tzinfo=UTC)
    for snapshot in (january, july):
        assert snapshot.fundamentals
        assert all(obs.as_of == snapshot.as_of for obs in snapshot.fundamentals)
