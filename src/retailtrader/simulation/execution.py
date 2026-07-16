"""Deterministic next-open execution model.

Timing model: signals are calculated after the prior session close; orders
fill at the incoming session's open plus configured slippage (basis points);
the portfolio is marked at that session's close. All accounting is Decimal,
quantized to cents.

Rules enforced here: sells before buys, integer shares, buys can never create
negative cash (quantities are capped, shortfalls recorded as rejections), and
symbols always process in ascending order.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from retailtrader.domain import (
    FillEvent,
    MarketSnapshot,
    OrderIntent,
    PortfolioSnapshot,
    Position,
    TargetPortfolio,
)

CENT = Decimal("0.01")
BPS_DENOMINATOR = Decimal(10_000)


@dataclass(frozen=True)
class RejectedOrder:
    symbol: str
    side: str
    requested_quantity: int
    reason: str


@dataclass(frozen=True)
class ExecutionResult:
    orders: tuple[OrderIntent, ...]
    rejections: tuple[RejectedOrder, ...]
    fills: tuple[FillEvent, ...]
    portfolio: PortfolioSnapshot


def fill_price(open_price: Decimal, side: str, slippage_bps: int) -> Decimal:
    """Session-open fill price adjusted by slippage: buys pay up, sells receive less."""
    adjustment = Decimal(slippage_bps) / BPS_DENOMINATOR
    multiplier = 1 + adjustment if side == "buy" else 1 - adjustment
    return (open_price * multiplier).quantize(CENT, rounding=ROUND_HALF_UP)


def execute_rebalance(
    portfolio: PortfolioSnapshot,
    target: TargetPortfolio,
    snapshot: MarketSnapshot,
    slippage_bps: int = 0,
    max_turnover: float | None = None,
) -> ExecutionResult:
    if max_turnover is not None and not 0 <= max_turnover <= 1:
        raise ValueError("max_turnover must be within [0, 1]")
    bars = {bar.symbol: bar for bar in snapshot.bars}
    held = {position.symbol: position.quantity for position in portfolio.positions}
    targeted = {position.symbol: position.weight for position in target.positions}

    missing = sorted((set(held) | set(targeted)) - set(bars))
    if missing:
        raise ValueError(f"snapshot missing bars for: {', '.join(missing)}")

    equity_at_open = portfolio.cash + sum(
        (Decimal(quantity) * bars[symbol].open for symbol, quantity in held.items()),
        Decimal(0),
    )
    desired = {
        symbol: int((equity_at_open * Decimal(str(weight))) / bars[symbol].open)
        for symbol, weight in targeted.items()
    }

    symbols = sorted(set(held) | set(desired))
    requested = [
        (
            symbol,
            "sell" if held.get(symbol, 0) > desired.get(symbol, 0) else "buy",
            abs(desired.get(symbol, 0) - held.get(symbol, 0)),
        )
        for symbol in symbols
        if desired.get(symbol, 0) != held.get(symbol, 0)
    ]

    cash = portfolio.cash
    quantities = dict(held)
    orders: list[OrderIntent] = []
    rejections: list[RejectedOrder] = []
    fills: list[FillEvent] = []

    trades = requested
    if max_turnover is not None and requested:
        requested_gross = sum(
            (
                Decimal(quantity) * fill_price(bars[symbol].open, side, slippage_bps)
                for symbol, side, quantity in requested
            ),
            Decimal(0),
        )
        turnover_budget = Decimal(2) * equity_at_open * Decimal(str(max_turnover))
        if requested_gross > turnover_budget:
            scale = turnover_budget / requested_gross
            trades = []
            for symbol, side, quantity in requested:
                allowed = int(Decimal(quantity) * scale)
                omitted = quantity - allowed
                if omitted:
                    rejections.append(
                        RejectedOrder(
                            symbol=symbol,
                            side=side,
                            requested_quantity=omitted,
                            reason="max turnover",
                        )
                    )
                if allowed:
                    trades.append((symbol, side, allowed))

    sells = [(symbol, quantity) for symbol, side, quantity in trades if side == "sell"]
    buys = [(symbol, quantity) for symbol, side, quantity in trades if side == "buy"]

    for symbol, quantity in sells:
        price = fill_price(bars[symbol].open, "sell", slippage_bps)
        orders.append(
            OrderIntent(
                run_id=portfolio.run_id,
                as_of=snapshot.as_of,
                symbol=symbol,
                side="sell",
                quantity=quantity,
            )
        )
        fills.append(
            FillEvent(
                run_id=portfolio.run_id,
                symbol=symbol,
                side="sell",
                quantity=quantity,
                fill_price=price,
                filled_at=snapshot.as_of,
            )
        )
        cash += Decimal(quantity) * price
        quantities[symbol] -= quantity

    for symbol, quantity in buys:
        price = fill_price(bars[symbol].open, "buy", slippage_bps)
        affordable = int(cash / price)
        fill_quantity = min(quantity, affordable)
        if fill_quantity < quantity:
            rejections.append(
                RejectedOrder(
                    symbol=symbol,
                    side="buy",
                    requested_quantity=quantity - fill_quantity,
                    reason="insufficient cash",
                )
            )
        if fill_quantity <= 0:
            continue
        orders.append(
            OrderIntent(
                run_id=portfolio.run_id,
                as_of=snapshot.as_of,
                symbol=symbol,
                side="buy",
                quantity=fill_quantity,
            )
        )
        fills.append(
            FillEvent(
                run_id=portfolio.run_id,
                symbol=symbol,
                side="buy",
                quantity=fill_quantity,
                fill_price=price,
                filled_at=snapshot.as_of,
            )
        )
        cash -= Decimal(fill_quantity) * price
        quantities[symbol] = quantities.get(symbol, 0) + fill_quantity

    positions = tuple(
        Position(
            symbol=symbol,
            quantity=quantity,
            price=bars[symbol].close,
            value=(Decimal(quantity) * bars[symbol].close).quantize(CENT),
        )
        for symbol, quantity in sorted(quantities.items())
        if quantity > 0
    )
    cash = cash.quantize(CENT)
    total_equity = (cash + sum((p.value for p in positions), Decimal(0))).quantize(CENT)
    marked = PortfolioSnapshot(
        run_id=portfolio.run_id,
        as_of=snapshot.as_of,
        cash=cash,
        positions=positions,
        total_equity=total_equity,
    )
    return ExecutionResult(
        orders=tuple(orders),
        rejections=tuple(rejections),
        fills=tuple(fills),
        portfolio=marked,
    )
