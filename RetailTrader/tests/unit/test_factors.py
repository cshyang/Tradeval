"""Invariant tests for the audited factor catalog.

Hand-calculated expected values on small deterministic inputs, plus the
catalog-wide invariants: missing data is never silently zero, price factors
exclude the execution bar, fundamentals respect point-in-time availability.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from retailtrader import factors
from retailtrader.domain import FundamentalObservation, MarketBar

AS_OF = datetime(2026, 1, 15, 20, 0, tzinfo=UTC)


def bars(symbol: str, closes: list[float], end: date = date(2026, 1, 15)) -> list[MarketBar]:
    """One bar per calendar day ending at ``end``, in chronological order."""
    start = end - timedelta(days=len(closes) - 1)
    return [
        MarketBar(
            symbol=symbol,
            session=start + timedelta(days=i),
            open=Decimal(str(close)),
            high=Decimal(str(close)),
            low=Decimal(str(close)),
            close=Decimal(str(close)),
            volume=1000,
        )
        for i, close in enumerate(closes)
    ]


def fund(
    symbol: str,
    metric: str,
    value: float,
    period_end: date,
    available_at: datetime | None = None,
) -> FundamentalObservation:
    available = available_at or datetime(
        period_end.year, period_end.month, period_end.day, tzinfo=UTC
    ) + timedelta(days=45)
    return FundamentalObservation(
        symbol=symbol,
        metric=metric,
        value=value,
        period_end=period_end,
        available_at=available,
        as_of=available,
    )


def annual_series(symbol: str, metric: str, values: list[float]) -> list[FundamentalObservation]:
    """Annual observations ending 2024, oldest first (all available by AS_OF)."""
    end_year = 2024
    return [
        fund(symbol, metric, v, date(end_year - (len(values) - 1 - i), 12, 31))
        for i, v in enumerate(values)
    ]


# --- price factors -----------------------------------------------------------


def test_momentum_6m_hand_calculated():
    obs = factors.momentum_6m("AAA", bars("AAA", [100.0 + i for i in range(127)]), AS_OF)
    assert obs.value == pytest.approx(226.0 / 100.0 - 1.0)


def test_momentum_6m_insufficient_history_is_unavailable_not_zero():
    obs = factors.momentum_6m("AAA", bars("AAA", [100.0] * 126), AS_OF)
    assert obs.value is None
    assert "insufficient history" in obs.unavailable_reason


def test_price_factors_exclude_the_execution_bar():
    history = bars("AAA", [100.0 + i for i in range(127)])
    execution_bar = MarketBar(
        symbol="AAA",
        session=AS_OF.date() + timedelta(days=1),
        open=Decimal("999999"),
        high=Decimal("999999"),
        low=Decimal("999999"),
        close=Decimal("999999"),
        volume=1000,
    )
    with_future = factors.momentum_6m("AAA", [*history, execution_bar], AS_OF)
    without_future = factors.momentum_6m("AAA", history, AS_OF)
    assert with_future.value == without_future.value


def test_momentum_12m_hand_calculated():
    obs = factors.momentum_12m("AAA", bars("AAA", [100.0 + i for i in range(253)]), AS_OF)
    assert obs.value == pytest.approx(352.0 / 100.0 - 1.0)


def test_above_sma_200_flat_series_is_zero():
    obs = factors.above_sma_200("AAA", bars("AAA", [100.0] * 200), AS_OF)
    assert obs.value == pytest.approx(0.0)


def test_above_sma_200_insufficient_history():
    obs = factors.above_sma_200("AAA", bars("AAA", [100.0] * 199), AS_OF)
    assert obs.value is None


def test_volatility_60d_constant_growth_is_zero():
    closes = [100.0 * 1.01**i for i in range(61)]
    obs = factors.volatility_60d("AAA", bars("AAA", closes), AS_OF)
    assert obs.value == pytest.approx(0.0, abs=1e-9)


def test_volatility_60d_insufficient_history():
    obs = factors.volatility_60d("AAA", bars("AAA", [100.0] * 60), AS_OF)
    assert obs.value is None


# --- fundamental factors -----------------------------------------------------


def test_roic_hand_calculated():
    rows = [
        fund("AAA", "nopat", 2e9, date(2025, 9, 30)),
        fund("AAA", "invested_capital", 1e10, date(2025, 9, 30)),
    ]
    assert factors.roic("AAA", rows, AS_OF).value == pytest.approx(0.2)


def test_roic_uses_latest_eligible_observation():
    rows = [
        fund("AAA", "nopat", 1e9, date(2024, 12, 31)),
        fund("AAA", "nopat", 2e9, date(2025, 9, 30)),
        fund("AAA", "invested_capital", 1e10, date(2025, 9, 30)),
    ]
    assert factors.roic("AAA", rows, AS_OF).value == pytest.approx(0.2)


def test_missing_fundamental_is_unavailable_not_zero():
    obs = factors.roic("AAA", [], AS_OF)
    assert obs.value is None
    assert "no nopat observation" in obs.unavailable_reason


def test_fundamentals_respect_point_in_time_availability():
    late = datetime(2026, 2, 1, tzinfo=UTC)  # after AS_OF: look-ahead
    rows = [
        fund("AAA", "nopat", 2e9, date(2025, 12, 31), available_at=late),
        fund("AAA", "invested_capital", 1e10, date(2025, 12, 31), available_at=late),
    ]
    assert factors.roic("AAA", rows, AS_OF).value is None


def test_fcf_yield_zero_market_cap_is_unavailable():
    rows = [
        fund("AAA", "free_cash_flow", 5e9, date(2025, 9, 30)),
        fund("AAA", "market_cap", 0.0, date(2025, 9, 30)),
    ]
    obs = factors.fcf_yield("AAA", rows, AS_OF)
    assert obs.value is None
    assert "non-positive market cap" in obs.unavailable_reason


def test_fcf_yield_hand_calculated():
    rows = [
        fund("AAA", "free_cash_flow", 5e9, date(2025, 9, 30)),
        fund("AAA", "market_cap", 1e11, date(2025, 9, 30)),
    ]
    assert factors.fcf_yield("AAA", rows, AS_OF).value == pytest.approx(0.05)


def test_debt_to_ebitda_hand_calculated_and_negative_ebitda():
    rows = [
        fund("AAA", "total_debt", 6e9, date(2025, 9, 30)),
        fund("AAA", "ebitda", 2e9, date(2025, 9, 30)),
    ]
    assert factors.debt_to_ebitda("AAA", rows, AS_OF).value == pytest.approx(3.0)

    negative = [
        fund("AAA", "total_debt", 6e9, date(2025, 9, 30)),
        fund("AAA", "ebitda", -1e9, date(2025, 9, 30)),
    ]
    assert factors.debt_to_ebitda("AAA", negative, AS_OF).value is None


def test_fcf_consistency_hand_calculated():
    rows = annual_series("AAA", "free_cash_flow", [5e9, -1e9, 3e9, 2e9])
    assert factors.free_cash_flow_consistency("AAA", rows, AS_OF).value == pytest.approx(0.75)


def test_fcf_consistency_insufficient_periods():
    rows = annual_series("AAA", "free_cash_flow", [5e9, 3e9, 2e9])
    assert factors.free_cash_flow_consistency("AAA", rows, AS_OF).value is None


def test_revenue_growth_3y_hand_calculated():
    rows = annual_series("AAA", "revenue", [100.0, 110.0, 121.0, 133.1])
    assert factors.revenue_growth_3y("AAA", rows, AS_OF).value == pytest.approx(0.1)


def test_eps_growth_3y_negative_base_is_unavailable():
    rows = annual_series("AAA", "eps", [-1.0, 1.1, 1.21, 1.331])
    obs = factors.eps_growth_3y("AAA", rows, AS_OF)
    assert obs.value is None
    assert "non-positive base eps" in obs.unavailable_reason


def test_growth_adjusted_pe_hand_calculated():
    rows = [
        *annual_series("AAA", "eps", [1.0, 1.1, 1.21, 1.331]),
        fund("AAA", "pe", 20.0, date(2025, 9, 30)),
    ]
    assert factors.growth_adjusted_pe("AAA", rows, AS_OF).value == pytest.approx(2.0)


def test_growth_adjusted_pe_non_positive_growth_is_unavailable():
    rows = [
        *annual_series("AAA", "eps", [2.0, 1.5, 1.2, 1.0]),
        fund("AAA", "pe", 20.0, date(2025, 9, 30)),
    ]
    obs = factors.growth_adjusted_pe("AAA", rows, AS_OF)
    assert obs.value is None
    assert "non-positive EPS growth" in obs.unavailable_reason


# --- catalog invariants ------------------------------------------------------


def test_catalog_has_the_eleven_audited_factors():
    assert factors.CATALOG == {
        "roic",
        "fcf_yield",
        "debt_to_ebitda",
        "free_cash_flow_consistency",
        "revenue_growth_3y",
        "eps_growth_3y",
        "growth_adjusted_pe",
        "momentum_6m",
        "momentum_12m",
        "above_sma_200",
        "volatility_60d",
    }


@pytest.mark.parametrize("name", sorted(factors.CATALOG))
def test_every_factor_reports_unavailable_on_empty_inputs(name: str):
    obs = factors.compute_factor(name, "AAA", AS_OF)
    assert obs.value is None
    assert obs.unavailable_reason
    assert obs.metric == name
    assert obs.formula_version == factors.FORMULA_VERSION


def test_compute_factor_rejects_unknown_name():
    with pytest.raises(KeyError):
        factors.compute_factor("sentiment_score", "AAA", AS_OF)
