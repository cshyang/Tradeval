from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from retailtrader.agent.contracts import (
    CapitalSpec,
    HorizonSpec,
    LimitSpec,
    MandateSpec,
    UniverseSpec,
)
from retailtrader.agent.evidence import EvidenceMetric
from retailtrader.agent.screening import ScreeningInput, screen_candidates

CUTOFF = datetime(2025, 1, 31, 21, tzinfo=UTC)
MARKET_HASH = "sha256:" + "a" * 64
METRIC_VALUES = {
    "revenue_growth": Decimal("0.08"),
    "free_cash_flow_margin": Decimal("0.25"),
    "return_on_assets": Decimal("0.20"),
    "debt_to_assets": Decimal("0.30"),
    "earnings_consistency": Decimal("1.0"),
    "price_to_free_cash_flow": Decimal("20"),
}


def mandate() -> MandateSpec:
    return MandateSpec(
        schema_version=1,
        experiment_id="exp-screen-001",
        capital=CapitalSpec(currency="USD", initial_cash="100000.00"),
        market="US",
        universe=UniverseSpec(
            symbols=("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "JPM", "META", "TSLA"),
            screener="price_quality_v1",
            max_candidates=3,
            minimum_history_sessions=250,
            minimum_average_dollar_volume="10000000",
            minimum_evidence_coverage=0.8,
            pinned_symbols=("JPM", "META"),
            excluded_symbols=("TSLA",),
        ),
        cadence="monthly",
        horizon=HorizonSpec(kind="hindsight", start=date(2024, 1, 1), end=date(2025, 1, 31)),
        limits=LimitSpec(
            minimum_cash_weight=0.05,
            maximum_position_weight=0.12,
            maximum_turnover=0.20,
            maximum_drawdown=0.25,
        ),
    )


def metrics(*, missing: set[str] | None = None) -> tuple[EvidenceMetric, ...]:
    unavailable = missing or set()
    return tuple(
        EvidenceMetric(
            name=name,
            value=None if name in unavailable else value,
            source_observation_ids=()
            if name in unavailable
            else (f"obs:{name}",),
            formula_version=f"{name}_v1",
            decision_cutoff=CUTOFF,
            unavailable_reason="missing" if name in unavailable else None,
        )
        for name, value in METRIC_VALUES.items()
    )


def record(
    symbol: str,
    *,
    history: int = 300,
    dollar_volume: str = "50000000",
    evidence: tuple[EvidenceMetric, ...] | None = None,
    supported: bool = True,
) -> ScreeningInput:
    return ScreeningInput(
        symbol=symbol,
        supported_security=supported,
        price_history_sessions=history,
        average_dollar_volume=Decimal(dollar_volume),
        latest_price=Decimal("100"),
        metrics=evidence or metrics(),
    )


def screening_inputs() -> tuple[ScreeningInput, ...]:
    return (
        record("AAPL"),
        record("MSFT"),
        record("NVDA", history=100),
        record("AMZN", dollar_volume="1000"),
        record("GOOGL", evidence=metrics(missing={"return_on_assets", "debt_to_assets"})),
        record("JPM", history=10, dollar_volume="1", evidence=metrics(missing={"return_on_assets"})),
        record("META", supported=False),
        record("TSLA"),
    )


def test_filters_ranks_and_applies_pin_exclude_overrides() -> None:
    result = screen_candidates(mandate(), CUTOFF, screening_inputs(), MARKET_HASH)

    assert [candidate.symbol for candidate in result.candidates] == ["JPM", "AAPL", "MSFT"]
    exclusions = {item.symbol: item.reason for item in result.exclusions}
    assert exclusions == {
        "AMZN": "average dollar volume below minimum",
        "GOOGL": "evidence coverage below minimum",
        "META": "unsupported security",
        "NVDA": "price history below minimum",
        "TSLA": "excluded by mandate",
    }
    assert result.candidates[0].evidence_coverage == pytest.approx(5 / 6)
    assert result.candidates[0].price_history_sessions == 10
    assert result.candidates[0].metrics[0].evidence_refs


def test_ties_use_stable_score_then_symbol_order_and_hash() -> None:
    first = screen_candidates(mandate(), CUTOFF, screening_inputs(), MARKET_HASH)
    second = screen_candidates(
        mandate(), CUTOFF, tuple(reversed(screening_inputs())), MARKET_HASH
    )

    assert first == second
    assert first.candidate_set_hash.startswith("sha256:")
    ranked = [candidate.symbol for candidate in first.candidates if candidate.symbol != "JPM"]
    assert ranked == ["AAPL", "MSFT"]


def test_rejects_duplicate_or_out_of_universe_inputs() -> None:
    with pytest.raises(ValueError, match="duplicate screening input"):
        screen_candidates(
            mandate(), CUTOFF, (*screening_inputs(), record("AAPL")), MARKET_HASH
        )
    with pytest.raises(ValueError, match="outside mandate universe"):
        screen_candidates(mandate(), CUTOFF, (record("XOM"),), MARKET_HASH)
