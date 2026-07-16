"""Benchmark metrics: engine-computed so the frontend never has to be."""

from datetime import date, timedelta

from retailtrader.evaluation.metrics import benchmark_metrics

PORTFOLIO_ONLY = (
    "turnover",
    "trade_count",
    "avg_holding_days",
    "cash_exposure",
    "max_concentration",
)


def _sessions(n: int) -> list[date]:
    return [date(2024, 1, 5) + timedelta(weeks=i) for i in range(n)]


def test_portfolio_only_metrics_are_none_not_zero():
    values = [100.0, 110.0, 121.0, 133.1]
    result = benchmark_metrics(
        values=values,
        sessions=_sessions(4),
        synthetic_mega_cap_proxy_values=values,
        equal_weight_values=values,
    )
    for key in PORTFOLIO_ONLY:
        assert result[key] is None


def test_total_return_and_relatives():
    proxy = [100.0, 110.0, 120.0, 130.0]
    other = [100.0, 105.0, 110.0, 115.0]
    result = benchmark_metrics(
        values=proxy,
        sessions=_sessions(4),
        synthetic_mega_cap_proxy_values=proxy,
        equal_weight_values=other,
    )
    assert result["total_return"] == 0.3
    assert result["synthetic_mega_cap_proxy_relative"] == 0.0
    assert result["equal_weight_relative"] == 0.15


def test_drawdown_is_negative_and_matches_series():
    values = [100.0, 120.0, 90.0, 110.0]
    result = benchmark_metrics(
        values=values,
        sessions=_sessions(4),
        synthetic_mega_cap_proxy_values=values,
        equal_weight_values=values,
    )
    assert result["max_drawdown"] == -0.25
