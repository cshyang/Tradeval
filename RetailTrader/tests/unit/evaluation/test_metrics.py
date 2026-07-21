"""Metric formulas against hand-calculated expected values, including the
zero-volatility and insufficient-history edge cases."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from retailtrader.domain import EvaluationReport
from retailtrader.evaluation.metrics import (
    EquityPoint,
    InsufficientHistoryError,
    annualized_volatility,
    avg_holding_days,
    cagr,
    cash_exposure,
    compute_evaluation,
    factor_coverage,
    max_concentration,
    max_drawdown,
    ranking_churn,
    read_equity_csv,
    selection_stability,
    sharpe_ratio,
    total_return,
    turnover,
)

D0 = date(2024, 1, 5)


def weekly_dates(count: int) -> list[date]:
    return [D0 + timedelta(days=7 * index) for index in range(count)]


def portfolio_row(session: date, cash: str, positions: list[tuple[str, int, str, str]]) -> dict:
    return {
        "as_of": datetime.combine(session, datetime.min.time(), tzinfo=UTC).isoformat(),
        "cash": cash,
        "positions": [
            {"symbol": symbol, "quantity": quantity, "price": price, "value": value}
            for symbol, quantity, price, value in positions
        ],
        "total_equity": "1000.00",
    }


def test_total_return_hand_calculated() -> None:
    assert total_return([100.0, 110.0, 121.0]) == pytest.approx(0.21)


def test_cagr_hand_calculated_over_731_days() -> None:
    # (121/100) ** (365.25/731) - 1
    dates = [date(2024, 1, 1), date(2026, 1, 1)]
    assert cagr([100.0, 121.0], dates) == pytest.approx(0.0999283, abs=1e-6)


def test_max_drawdown_hand_calculated() -> None:
    assert max_drawdown([100.0, 120.0, 90.0, 100.0, 80.0]) == pytest.approx(-1 / 3)


def test_annualized_volatility_hand_calculated_weekly() -> None:
    # Returns +10%, -10%: sample stdev 0.1414214, annualized by sqrt(365.25/7).
    assert annualized_volatility([100.0, 110.0, 99.0], weekly_dates(3)) == pytest.approx(
        1.0215534, abs=1e-6
    )


def test_zero_volatility_yields_zero_sharpe_and_volatility() -> None:
    values = [100.0, 200.0, 400.0]  # exactly +100% per period
    dates = weekly_dates(3)
    assert annualized_volatility(values, dates) == 0.0
    assert sharpe_ratio(values, dates) == 0.0


def test_insufficient_history_raises() -> None:
    with pytest.raises(InsufficientHistoryError):
        total_return([100.0])
    with pytest.raises(InsufficientHistoryError):
        annualized_volatility([100.0, 110.0], weekly_dates(2))
    with pytest.raises(InsufficientHistoryError):
        cagr([100.0, 110.0], [D0, D0])


def test_turnover_skips_the_initial_funding_session() -> None:
    points = [
        EquityPoint(D0, 1000.0, 1000.0, 1000.0),
        EquityPoint(D0 + timedelta(days=7), 1000.0, 1000.0, 1000.0),
    ]
    fills = [
        {"filled_at": points[0].session.isoformat(), "quantity": 100, "fill_price": "10.00"},
        {"filled_at": points[1].session.isoformat(), "quantity": 10, "fill_price": "5.00"},
        {"filled_at": points[1].session.isoformat(), "quantity": 10, "fill_price": "5.00"},
    ]
    # Second session notional 100 / 2 / equity 1000 = 0.05.
    assert turnover(fills, points) == pytest.approx(0.05)
    assert turnover(fills[:1], points) == 0.0


def test_equity_csv_uses_explicit_synthetic_proxy_column(tmp_path) -> None:
    path = tmp_path / "equity.csv"
    path.write_text(
        "date,equity,synthetic_mega_cap_proxy_equity,equal_weight_equity\n"
        "2024-01-05,1000.00,1010.00,990.00\n"
    )
    point = read_equity_csv(path)[0]
    assert point.synthetic_mega_cap_proxy_equity == 1010.0


def test_avg_holding_days_hand_calculated() -> None:
    rows = [
        portfolio_row(D0, "0.00", [("AAA", 1, "1.00", "1.00")]),
        portfolio_row(
            D0 + timedelta(days=7),
            "0.00",
            [("AAA", 1, "1.00", "1.00"), ("BBB", 1, "1.00", "1.00")],
        ),
        portfolio_row(D0 + timedelta(days=14), "0.00", [("BBB", 1, "1.00", "1.00")]),
    ]
    # AAA held 14 days (exited), BBB held 7 days (still open): mean 10.5.
    assert avg_holding_days(rows) == pytest.approx(10.5)


def test_cash_exposure_and_max_concentration() -> None:
    rows = [
        portfolio_row(D0, "50.00", [("AAA", 1, "300.00", "300.00")]),
        portfolio_row(D0 + timedelta(days=7), "100.00", [("AAA", 1, "800.00", "800.00")]),
    ]
    assert cash_exposure(rows) == pytest.approx(0.075)
    assert max_concentration(rows) == pytest.approx(0.8)


def test_fidelity_metrics_from_decision_records() -> None:
    decisions = [
        {
            "selected": [
                {
                    "symbol": "AAA",
                    "factors": [{"name": "f", "value": 1.0}, {"name": "g", "value": None}],
                },
                {"symbol": "BBB", "factors": [{"name": "f", "value": 2.0}]},
            ]
        },
        {"selected": [{"symbol": "BBB", "factors": []}, {"symbol": "CCC", "factors": []}]},
    ]
    assert factor_coverage(decisions) == pytest.approx(2 / 3)
    # {AAA,BBB} -> {BBB,CCC}: half the selection replaced, Jaccard overlap 1/3.
    assert ranking_churn(decisions) == pytest.approx(0.5)
    assert selection_stability(decisions) == pytest.approx(1 / 3)


def test_compute_evaluation_assembles_a_domain_report() -> None:
    sessions = weekly_dates(3)
    equity = [
        EquityPoint(sessions[0], 1000.0, 1000.0, 1000.0),
        EquityPoint(sessions[1], 1100.0, 1050.0, 1010.0),
        EquityPoint(sessions[2], 1210.0, 1100.0, 1020.0),
    ]
    fills = [
        {"filled_at": sessions[0].isoformat(), "quantity": 10, "fill_price": "100.00"},
        {"filled_at": sessions[1].isoformat(), "quantity": 1, "fill_price": "110.00"},
    ]
    portfolios = [portfolio_row(sessions[0], "50.00", [("AAA", 1, "300.00", "300.00")])]
    report = compute_evaluation(
        run_id="run-test",
        as_of=datetime(2024, 1, 19, 20, tzinfo=UTC),
        equity=equity,
        fills=fills,
        portfolios=portfolios,
        decisions=[],
        constraint_interventions=2,
    )
    assert isinstance(report, EvaluationReport)
    assert report.metrics.total_return == pytest.approx(0.21)
    assert report.metrics.trade_count == 2
    assert report.metrics.synthetic_mega_cap_proxy_relative == pytest.approx(0.21 - 0.10)
    assert report.metrics.equal_weight_relative == pytest.approx(0.21 - 0.02)
    assert report.metrics.turnover == pytest.approx(110.0 / 2 / 1100.0)
    assert report.fidelity.constraint_interventions == 2
    assert report.fidelity.rule_violations == 0
