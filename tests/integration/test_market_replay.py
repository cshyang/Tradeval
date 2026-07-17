"""Offline end-to-end real-price-shaped trend replay."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from retailtrader.cli import (
    INITIAL_CASH,
    _identity_hash,
    _run_market_replay,
)
from retailtrader.data.cache import PriceFetchResult
from retailtrader.data.protocol import AvailableMarketBar, PriceBatch, PriceQuery
from retailtrader.domain import MarketBar
from retailtrader.storage.artifacts import read_jsonl

NY = ZoneInfo("America/New_York")


class FixtureLoader:
    def __init__(self, *, history_limit: int | None = None) -> None:
        self.calls: list[PriceQuery] = []
        self.history_limit = history_limit

    def fetch(self, query: PriceQuery) -> PriceFetchResult:
        self.calls.append(query)
        sessions = []
        session = query.start
        while session <= query.end:
            if session.weekday() < 5:
                sessions.append(session)
            session += timedelta(days=1)
        if self.history_limit is not None:
            sessions = sessions[-self.history_limit :]
        observations = []
        for day_index, session in enumerate(sessions):
            for symbol_index, symbol in enumerate(query.symbols):
                base = Decimal(50 + symbol_index * 3) + Decimal(day_index) / Decimal(10)
                open_price = base
                close_price = base + Decimal("0.08") + Decimal(symbol_index % 7) / Decimal(100)
                observations.append(
                    AvailableMarketBar(
                        bar=MarketBar(
                            symbol=symbol,
                            session=session,
                            open=open_price,
                            high=close_price + Decimal("0.05"),
                            low=open_price - Decimal("0.05"),
                            close=close_price,
                            volume=1_000_000 + symbol_index,
                        ),
                        open_available_at=datetime.combine(
                            session, time(9, 30), tzinfo=NY
                        ),
                        close_available_at=datetime.combine(
                            session, time(16), tzinfo=NY
                        ),
                        source_ref=f"fixture:{symbol}:{session}",
                    )
                )
        batch = PriceBatch.create(
            transport="openbb",
            provider="yfinance",
            query=query,
            observations=tuple(observations),
            retrieved_at=datetime.combine(
                query.end + timedelta(days=1), time(12), tzinfo=UTC
            ),
            raw_hash="d" * 64,
            provider_versions=(("openbb", "4.7.2"), ("openbb-yfinance", "1.6.3")),
        )
        return PriceFetchResult(batch=batch, cache_status="bypass")


def test_market_replay_uses_real_price_shape_fake_cash_and_provenance(
    tmp_path: Path,
) -> None:
    loader = FixtureLoader()
    workspace = tmp_path / "market"

    run_dir = _run_market_replay(
        loader=loader,
        workspace=workspace,
        start=date(2024, 3, 1),
        end=date(2024, 4, 30),
    )

    assert len(loader.calls) == 1
    query = loader.calls[0]
    assert "SPY" in query.symbols
    assert query.adjustment == "splits_and_dividends"
    assert query.start == date(2024, 3, 1) - timedelta(days=400)
    assert query.end == date(2024, 4, 30)

    provenance = json.loads((run_dir / "data-provenance.json").read_text())
    assert provenance["kind"] == "real_market"
    assert provenance["validity"] == "hindsight_current_universe"
    assert provenance["label"] == "HINDSIGHT · ADJUSTED MARKET DATA"
    assert provenance["transport"] == "openbb"
    assert provenance["provider"] == "yfinance"
    assert provenance["adjustment"] == "splits_and_dividends"
    assert provenance["benchmark_kind"] == "no_cost_reference"
    assert provenance["reference_method_version"] == "execution_open_fixed_basket_v1"
    assert len(provenance["normalized_hash"]) == 64
    assert provenance["run_identity_hash"] == _identity_hash(
        provenance["run_identity"]
    )
    assert provenance["warnings"]

    decisions = read_jsonl(run_dir / "decisions.jsonl")
    fills = read_jsonl(run_dir / "fills.jsonl")
    portfolios = read_jsonl(run_dir / "portfolio.jsonl")
    equity_rows = (run_dir / "equity.csv").read_text().strip().splitlines()[1:]
    assert len(decisions) == len(portfolios) == len(equity_rows)
    assert len(decisions) >= 3
    assert fills
    assert datetime.fromisoformat(decisions[0]["as_of"]) < datetime.fromisoformat(
        fills[0]["filled_at"]
    )
    assert datetime.fromisoformat(fills[0]["filled_at"]) < datetime.fromisoformat(
        portfolios[0]["as_of"]
    )
    assert Decimal(portfolios[0]["cash"]) >= 0
    assert INITIAL_CASH == Decimal("100000")
    report = (run_dir / "report.md").read_text()
    assert "hindsight_current_universe" in report
    assert "normalized research prices" in report
    assert (workspace / "comparison.md").exists()


def test_market_run_identity_changes_with_calculation_inputs() -> None:
    base = {
        "requested_start": date(2024, 1, 1),
        "normalized_hash": "a" * 64,
        "slippage_bps": 5,
    }
    assert _identity_hash(base) == _identity_hash(dict(base))
    assert _identity_hash(base) != _identity_hash(base | {"slippage_bps": 6})
    assert _identity_hash(base) != _identity_hash(
        base | {"normalized_hash": "b" * 64}
    )


def test_market_replay_rejects_insufficient_warmup_before_writing(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "market"

    try:
        _run_market_replay(
            loader=FixtureLoader(history_limit=100),
            workspace=workspace,
            start=date(2024, 3, 1),
            end=date(2024, 4, 30),
        )
    except ValueError as exc:
        assert "need 253 completed sessions" in str(exc)
    else:  # pragma: no cover - explicit assertion message
        raise AssertionError("insufficient warmup unexpectedly succeeded")

    assert not workspace.exists()
