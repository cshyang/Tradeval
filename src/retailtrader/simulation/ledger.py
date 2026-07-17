"""Ledger replay: reconstruct portfolio state from the append-only event log.

Replaying `portfolio_created`, `order_filled`, and `portfolio_marked` events
rebuilds cash, integer positions, average-cost basis, and marked equity
exactly. `portfolio_marked` payloads are cross-checked against the replayed
state so any divergence fails loudly instead of drifting.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

CENT = Decimal("0.01")


class LedgerReplayError(ValueError):
    """The event stream is internally inconsistent."""


@dataclass
class LedgerState:
    cash: Decimal = Decimal("0")
    positions: dict[str, int] = field(default_factory=dict)
    cost_basis: dict[str, Decimal] = field(default_factory=dict)
    equity: Decimal | None = None
    last_marked_as_of: str | None = None


def replay_events(events: Iterable[Mapping[str, Any]]) -> LedgerState:
    state = LedgerState()
    for event in events:
        event_type = event["event_type"]
        payload = event["payload"]
        if event_type == "portfolio_created":
            state.cash = Decimal(payload["cash"])
            state.equity = state.cash
        elif event_type == "order_filled":
            _apply_fill(state, payload)
        elif event_type == "portfolio_marked":
            _apply_mark(state, event["as_of"], payload)
    return state


def _apply_fill(state: LedgerState, payload: Mapping[str, Any]) -> None:
    symbol = payload["symbol"]
    quantity = int(payload["quantity"])
    price = Decimal(payload["fill_price"])
    notional = Decimal(quantity) * price
    held = state.positions.get(symbol, 0)

    if payload["side"] == "buy":
        if notional > state.cash:
            raise LedgerReplayError(f"buy of {symbol} would make cash negative")
        state.cash -= notional
        state.positions[symbol] = held + quantity
        state.cost_basis[symbol] = state.cost_basis.get(symbol, Decimal("0")) + notional
        return

    if quantity > held:
        raise LedgerReplayError(f"sell of {quantity} {symbol} exceeds held {held}")
    state.cash += notional
    remaining = held - quantity
    basis = state.cost_basis.get(symbol, Decimal("0"))
    if remaining == 0:
        state.positions.pop(symbol)
        state.cost_basis.pop(symbol, None)
    else:
        state.positions[symbol] = remaining
        sold_basis = (basis * Decimal(quantity) / Decimal(held)).quantize(CENT)
        state.cost_basis[symbol] = basis - sold_basis


def _apply_mark(state: LedgerState, as_of: str, payload: Mapping[str, Any]) -> None:
    marked_positions = {row["symbol"]: int(row["quantity"]) for row in payload["positions"]}
    if marked_positions != state.positions:
        raise LedgerReplayError(
            f"marked positions {marked_positions} diverge from ledger {state.positions}"
        )
    if Decimal(payload["cash"]) != state.cash.quantize(CENT):
        raise LedgerReplayError(
            f"marked cash {payload['cash']} diverges from ledger {state.cash}"
        )
    marked_values: dict[str, Decimal] = {}
    for row in payload["positions"]:
        symbol = row["symbol"]
        price = Decimal(row["price"])
        expected_value = (Decimal(row["quantity"]) * price).quantize(CENT)
        marked_value = Decimal(row["value"])
        if marked_value != expected_value:
            raise LedgerReplayError(
                f"marked value {marked_value} for {symbol} diverges from "
                f"quantity-times-price {expected_value}"
            )
        marked_values[symbol] = marked_value
    # The engine rounds each position to cents before summing portfolio equity.
    # Repeating that exact path avoids a separate aggregate-rounding result.
    equity = state.cash + sum(marked_values.values(), Decimal(0))
    state.equity = equity.quantize(CENT)
    if Decimal(payload["total_equity"]) != state.equity:
        raise LedgerReplayError(
            f"marked equity {payload['total_equity']} diverges from ledger {state.equity}"
        )
    state.last_marked_as_of = as_of
