"""Explicitly opted-in smoke test for OpenBB's Yahoo Finance provider."""

from __future__ import annotations

import os
from datetime import UTC, date

import pytest

from retailtrader.data.openbb import OpenBBYFinancePriceSource
from retailtrader.data.protocol import PriceQuery


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RETAILTRADER_LIVE_DATA") != "1",
    reason="set RETAILTRADER_LIVE_DATA=1 to access OpenBB/Yahoo",
)
def test_openbb_yfinance_completed_adjusted_daily_bars() -> None:
    query = PriceQuery(("AAPL",), date(2025, 1, 2), date(2025, 1, 8))

    batch = OpenBBYFinancePriceSource().fetch(query)

    assert batch.transport == "openbb"
    assert batch.provider == "yfinance"
    assert batch.query.adjustment == "splits_and_dividends"
    assert batch.observations
    assert list(batch.observations) == sorted(
        batch.observations, key=lambda item: (item.bar.session, item.bar.symbol)
    )
    assert all(item.open_available_at.tzinfo == UTC for item in batch.observations)
    assert all(item.close_available_at.tzinfo == UTC for item in batch.observations)
    assert all(item.open_available_at < item.close_available_at for item in batch.observations)
