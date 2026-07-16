"""Execution invariants: next-open fills, slippage, sells-first, integer
shares, no negative cash, stable ordering."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from retailtrader.domain import PortfolioSnapshot, Position, TargetPortfolio, TargetPosition
from retailtrader.simulation.execution import execute_rebalance, fill_price
from tests.helpers import RUN_ID, close_dt, make_snapshot, open_dt

DAY0 = date(2024, 1, 5)
DAY1 = date(2024, 1, 8)
EXECUTION_AT = open_dt(DAY1)


def cash_portfolio(cash: str) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        run_id=RUN_ID,
        as_of=close_dt(DAY0),
        cash=Decimal(cash),
        positions=(),
        total_equity=Decimal(cash),
    )


def make_target(weights: dict[str, float], cash_weight: float) -> TargetPortfolio:
    return TargetPortfolio(
        run_id=RUN_ID,
        as_of=close_dt(DAY1),
        cash_weight=cash_weight,
        positions=tuple(
            TargetPosition(symbol=symbol, weight=weight) for symbol, weight in weights.items()
        ),
    )


def test_fill_price_applies_slippage_by_side() -> None:
    assert fill_price(Decimal("10.00"), "buy", 100) == Decimal("10.10")
    assert fill_price(Decimal("10.00"), "sell", 100) == Decimal("9.90")
    assert fill_price(Decimal("10.00"), "buy", 0) == Decimal("10.00")


def test_buys_fill_at_open_with_integer_shares() -> None:
    snapshot = make_snapshot(DAY1, {"AAA": ("10.00", "11.00"), "BBB": ("20.00", "19.00")})
    result = execute_rebalance(
        cash_portfolio("1000.00"),
        make_target({"AAA": 0.5, "BBB": 0.4}, cash_weight=0.1),
        snapshot,
        filled_at=EXECUTION_AT,
    )
    assert [(f.symbol, f.side, f.quantity, f.fill_price) for f in result.fills] == [
        ("AAA", "buy", 50, Decimal("10.00")),
        ("BBB", "buy", 20, Decimal("20.00")),
    ]
    assert result.portfolio.cash == Decimal("100.00")
    # Marked at session close, not at the fill price.
    assert result.portfolio.positions[0].value == Decimal("550.00")
    assert result.portfolio.total_equity == Decimal("1030.00")
    assert all(fill.filled_at == EXECUTION_AT for fill in result.fills)
    assert all(order.as_of == EXECUTION_AT for order in result.orders)
    assert result.portfolio.as_of == snapshot.as_of


def test_slippage_raises_buy_cost_but_not_sizing() -> None:
    snapshot = make_snapshot(DAY1, {"AAA": ("10.00", "10.00"), "BBB": ("20.00", "20.00")})
    result = execute_rebalance(
        cash_portfolio("1000.00"),
        make_target({"AAA": 0.5, "BBB": 0.4}, cash_weight=0.1),
        snapshot,
        filled_at=EXECUTION_AT,
        slippage_bps=100,
    )
    assert [(f.quantity, f.fill_price) for f in result.fills] == [
        (50, Decimal("10.10")),
        (20, Decimal("20.20")),
    ]
    assert result.portfolio.cash == Decimal("1000.00") - Decimal("505.00") - Decimal("404.00")


def test_sells_execute_before_buys_and_fund_them() -> None:
    portfolio = PortfolioSnapshot(
        run_id=RUN_ID,
        as_of=close_dt(DAY0),
        cash=Decimal("10.00"),
        positions=(
            Position(symbol="AAA", quantity=50, price=Decimal("10.00"), value=Decimal("500.00")),
        ),
        total_equity=Decimal("510.00"),
    )
    snapshot = make_snapshot(DAY1, {"AAA": ("10.00", "10.00"), "BBB": ("10.00", "10.00")})
    result = execute_rebalance(
        portfolio, make_target({"BBB": 0.95}, cash_weight=0.05), snapshot, filled_at=EXECUTION_AT
    )
    assert [(f.symbol, f.side, f.quantity) for f in result.fills] == [
        ("AAA", "sell", 50),
        ("BBB", "buy", 48),
    ]
    assert result.portfolio.cash == Decimal("30.00")
    assert [p.symbol for p in result.portfolio.positions] == ["BBB"]


def test_buy_is_capped_and_shortfall_rejected_so_cash_never_goes_negative() -> None:
    snapshot = make_snapshot(DAY1, {"AAA": ("10.00", "10.00"), "BBB": ("10.00", "10.00")})
    result = execute_rebalance(
        cash_portfolio("1000.00"),
        make_target({"AAA": 0.5, "BBB": 0.5}, cash_weight=0.0),
        snapshot,
        filled_at=EXECUTION_AT,
        slippage_bps=200,
    )
    # Sized at open: 50 each. AAA fills first at 10.20 (510.00), leaving 490.00,
    # which affords only 48 BBB shares.
    assert [(f.symbol, f.quantity) for f in result.fills] == [("AAA", 50), ("BBB", 48)]
    assert [(r.symbol, r.requested_quantity, r.reason) for r in result.rejections] == [
        ("BBB", 2, "insufficient cash")
    ]
    assert result.portfolio.cash == Decimal("0.40")
    assert result.portfolio.cash >= 0


def test_unaffordable_buy_is_fully_rejected() -> None:
    snapshot = make_snapshot(DAY1, {"AAA": ("10.00", "10.00"), "BBB": ("60.00", "60.00")})
    result = execute_rebalance(
        cash_portfolio("1000.00"),
        make_target({"AAA": 0.94, "BBB": 0.06}, cash_weight=0.0),
        snapshot,
        filled_at=EXECUTION_AT,
        slippage_bps=100,
    )
    assert [(f.symbol, f.quantity) for f in result.fills] == [("AAA", 94)]
    assert [(r.symbol, r.requested_quantity, r.reason) for r in result.rejections] == [
        ("BBB", 1, "insufficient cash")
    ]
    assert result.portfolio.cash == Decimal("50.60")


def test_symbols_process_in_stable_order_regardless_of_target_order() -> None:
    snapshot = make_snapshot(
        DAY1,
        {"AAA": ("10.00", "10.00"), "BBB": ("10.00", "10.00"), "CCC": ("10.00", "10.00")},
    )
    target = make_target({"CCC": 0.3, "AAA": 0.3, "BBB": 0.3}, cash_weight=0.1)
    result = execute_rebalance(cash_portfolio("1000.00"), target, snapshot, filled_at=EXECUTION_AT)
    assert [f.symbol for f in result.fills] == ["AAA", "BBB", "CCC"]
    assert [p.symbol for p in result.portfolio.positions] == ["AAA", "BBB", "CCC"]


def test_positions_missing_from_target_are_fully_sold() -> None:
    portfolio = PortfolioSnapshot(
        run_id=RUN_ID,
        as_of=close_dt(DAY0),
        cash=Decimal("0.00"),
        positions=(
            Position(symbol="CCC", quantity=10, price=Decimal("10.00"), value=Decimal("100.00")),
        ),
        total_equity=Decimal("100.00"),
    )
    snapshot = make_snapshot(DAY1, {"AAA": ("10.00", "10.00"), "CCC": ("10.00", "10.00")})
    result = execute_rebalance(
        portfolio, make_target({"AAA": 0.9}, cash_weight=0.1), snapshot, filled_at=EXECUTION_AT
    )
    assert result.fills[0].symbol == "CCC"
    assert result.fills[0].side == "sell"
    assert result.fills[0].quantity == 10
    assert "CCC" not in [p.symbol for p in result.portfolio.positions]


def test_missing_bar_for_held_or_targeted_symbol_raises() -> None:
    snapshot = make_snapshot(DAY1, {"AAA": ("10.00", "10.00")})
    with pytest.raises(ValueError, match="missing bars for: BBB"):
        execute_rebalance(
            cash_portfolio("1000.00"),
            make_target({"AAA": 0.5, "BBB": 0.4}, cash_weight=0.1),
            snapshot,
            filled_at=EXECUTION_AT,
        )
