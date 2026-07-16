"""Event log and ledger invariants: append-only JSONL, Decimal-as-string
serialization, and exact replay of cash, positions, cost basis, and equity."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from retailtrader.simulation.ledger import LedgerReplayError, replay_events
from retailtrader.storage.events import EventLog, to_jsonable

AS_OF = datetime(2024, 1, 8, 20, tzinfo=UTC)


def event(event_type: str, payload: dict) -> dict:
    return {
        "schema_version": 1,
        "run_id": "run-test",
        "event_type": event_type,
        "as_of": AS_OF.isoformat(),
        "created_at": AS_OF.isoformat(),
        "payload": payload,
    }


def fill(side: str, quantity: int, price: str, symbol: str = "AAA") -> dict:
    return event(
        "order_filled",
        {
            "run_id": "run-test",
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "fill_price": price,
            "filled_at": AS_OF.isoformat(),
        },
    )


def test_event_log_appends_and_reads_in_order(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl", "run-test")
    log.append("portfolio_created", AS_OF, {"cash": Decimal("1000.00")})
    log.append("rebalance_completed", AS_OF, {"session": AS_OF.date()})
    events = log.read()
    assert [item["event_type"] for item in events] == [
        "portfolio_created",
        "rebalance_completed",
    ]
    assert events[0]["run_id"] == "run-test"
    assert log.completed_sessions() == {AS_OF.isoformat()}


def test_decimals_serialize_as_strings_in_the_raw_file(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl", "run-test")
    log.append("portfolio_created", AS_OF, {"cash": Decimal("1000.00")})
    raw = (tmp_path / "events.jsonl").read_text()
    assert '"cash": "1000.00"' in raw
    assert to_jsonable({"price": Decimal("10.10")}) == {"price": "10.10"}


def test_unknown_event_type_is_rejected(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl", "run-test")
    with pytest.raises(ValueError, match="unknown event type"):
        log.append("portfolio_deleted", AS_OF, {})


def test_replay_reconstructs_cash_positions_cost_basis_and_equity_exactly() -> None:
    events = [
        event("portfolio_created", {"cash": "1000.00"}),
        fill("buy", 10, "10.00"),
        fill("buy", 10, "20.00"),
        fill("sell", 5, "30.00"),
        event(
            "portfolio_marked",
            {
                "as_of": AS_OF.isoformat(),
                "cash": "850.00",
                "positions": [
                    {"symbol": "AAA", "quantity": 15, "price": "12.00", "value": "180.00"}
                ],
                "total_equity": "1030.00",
            },
        ),
    ]
    state = replay_events(events)
    assert state.cash == Decimal("850.00")
    assert state.positions == {"AAA": 15}
    # Average-cost basis: 300.00 total, minus 5/20 sold => 225.00 remains.
    assert state.cost_basis == {"AAA": Decimal("225.00")}
    assert state.equity == Decimal("1030.00")
    assert state.last_marked_as_of == AS_OF.isoformat()


def test_selling_the_full_position_clears_basis() -> None:
    state = replay_events(
        [
            event("portfolio_created", {"cash": "100.00"}),
            fill("buy", 5, "10.00"),
            fill("sell", 5, "12.00"),
        ]
    )
    assert state.positions == {}
    assert state.cost_basis == {}
    assert state.cash == Decimal("110.00")


def test_replay_rejects_sell_exceeding_held_quantity() -> None:
    with pytest.raises(LedgerReplayError, match="exceeds held"):
        replay_events([event("portfolio_created", {"cash": "100.00"}), fill("sell", 1, "10.00")])


def test_replay_rejects_buy_that_would_make_cash_negative() -> None:
    with pytest.raises(LedgerReplayError, match="cash negative"):
        replay_events([event("portfolio_created", {"cash": "10.00"}), fill("buy", 2, "10.00")])


def test_replay_rejects_divergent_portfolio_mark() -> None:
    events = [
        event("portfolio_created", {"cash": "1000.00"}),
        fill("buy", 10, "10.00"),
        event(
            "portfolio_marked",
            {
                "as_of": AS_OF.isoformat(),
                "cash": "900.00",
                "positions": [
                    {"symbol": "AAA", "quantity": 11, "price": "10.00", "value": "110.00"}
                ],
                "total_equity": "1010.00",
            },
        ),
    ]
    with pytest.raises(LedgerReplayError, match="diverge"):
        replay_events(events)
