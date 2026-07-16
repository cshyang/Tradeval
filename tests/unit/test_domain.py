"""Invariant tests for the frozen domain contracts."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from retailtrader.domain import (
    FactorObservation,
    FundamentalObservation,
    OrderIntent,
    PortfolioSnapshot,
    Position,
    TargetPortfolio,
    TargetPosition,
)

AS_OF = datetime(2026, 7, 16, tzinfo=UTC)


def test_rejects_future_fundamental_observation():
    with pytest.raises(ValidationError, match="look-ahead"):
        FundamentalObservation(
            symbol="AAPL",
            metric="revenue",
            value=100,
            period_end=date(2026, 6, 30),
            available_at=datetime(2026, 8, 1, tzinfo=UTC),
            as_of=AS_OF,
        )


def test_accepts_available_fundamental_observation():
    obs = FundamentalObservation(
        symbol="AAPL",
        metric="revenue",
        value=100,
        period_end=date(2026, 3, 31),
        available_at=datetime(2026, 5, 15, tzinfo=UTC),
        as_of=AS_OF,
        availability_source="approximated",
    )
    assert obs.available_at <= obs.as_of


def test_rejects_naive_datetime():
    with pytest.raises(ValidationError, match="timezone-aware"):
        FundamentalObservation(
            symbol="AAPL",
            metric="revenue",
            value=100,
            period_end=date(2026, 3, 31),
            available_at=datetime(2026, 5, 15),
            as_of=AS_OF,
        )


def test_rejects_leveraged_target_portfolio():
    with pytest.raises(ValidationError, match="total 1.0"):
        TargetPortfolio(
            run_id="run-1",
            as_of=AS_OF,
            cash_weight=0,
            positions=(
                TargetPosition(symbol="AAPL", weight=0.7),
                TargetPosition(symbol="MSFT", weight=0.7),
            ),
        )


def test_rejects_duplicate_target_symbols():
    with pytest.raises(ValidationError, match="duplicate"):
        TargetPortfolio(
            run_id="run-1",
            as_of=AS_OF,
            cash_weight=0.2,
            positions=(
                TargetPosition(symbol="AAPL", weight=0.4),
                TargetPosition(symbol="AAPL", weight=0.4),
            ),
        )


def test_accepts_valid_target_portfolio():
    portfolio = TargetPortfolio(
        run_id="run-1",
        as_of=AS_OF,
        cash_weight=0.05,
        positions=(
            TargetPosition(symbol="AAPL", weight=0.475),
            TargetPosition(symbol="MSFT", weight=0.475),
        ),
    )
    assert len(portfolio.positions) == 2


def test_rejects_short_position_weight():
    with pytest.raises(ValidationError):
        TargetPosition(symbol="AAPL", weight=-0.1)


def test_factor_observation_requires_value_xor_reason():
    with pytest.raises(ValidationError, match="exactly one"):
        FactorObservation(
            symbol="AAPL",
            metric="roic",
            value=0.21,
            unavailable_reason="missing filings",
            formula_version="v1",
            as_of=AS_OF,
        )
    missing = FactorObservation(
        symbol="AAPL",
        metric="roic",
        unavailable_reason="missing filings",
        formula_version="v1",
        as_of=AS_OF,
    )
    assert missing.value is None


def test_rejects_non_positive_order_quantity():
    with pytest.raises(ValidationError, match="positive integer"):
        OrderIntent(run_id="run-1", as_of=AS_OF, symbol="AAPL", side="buy", quantity=0)


def test_rejects_negative_cash_snapshot():
    with pytest.raises(ValidationError, match="non-negative"):
        PortfolioSnapshot(
            run_id="run-1",
            as_of=AS_OF,
            cash=Decimal("-1"),
            positions=(
                Position(
                    symbol="AAPL",
                    quantity=10,
                    price=Decimal("200"),
                    value=Decimal("2000"),
                ),
            ),
            total_equity=Decimal("1999"),
        )
