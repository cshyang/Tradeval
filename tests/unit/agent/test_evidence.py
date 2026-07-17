from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from retailtrader.agent.evidence import derive_evidence
from retailtrader.data.sec import normalize_company_facts

FIXTURE = Path(__file__).parents[2] / "fixtures/market_data/sec_companyfacts_aapl.json"


def observations():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return normalize_company_facts(payload)


def test_derives_point_in_time_quality_metrics_with_source_ids() -> None:
    cutoff = datetime(2024, 1, 31, 21, tzinfo=UTC)
    metrics = {
        metric.name: metric
        for metric in derive_evidence(
            observations(),
            cutoff,
            price=Decimal("190.00"),
            price_observation_id="price:AAPL:2024-01-31:close",
        )
    }

    assert set(metrics) == {
        "revenue_growth",
        "free_cash_flow_margin",
        "return_on_assets",
        "debt_to_assets",
        "earnings_consistency",
        "price_to_free_cash_flow",
    }
    assert metrics["revenue_growth"].value == Decimal("-0.028005")
    assert metrics["free_cash_flow_margin"].value == Decimal("0.259817")
    assert metrics["return_on_assets"].value == Decimal("0.275031")
    assert metrics["debt_to_assets"].value == Decimal("0.270237")
    assert metrics["earnings_consistency"].value == Decimal("1.000000")
    assert metrics["price_to_free_cash_flow"].value == Decimal("30.169344")
    assert metrics["price_to_free_cash_flow"].source_observation_ids[-1] == (
        "price:AAPL:2024-01-31:close"
    )
    assert all(metric.decision_cutoff == cutoff for metric in metrics.values())
    assert all(metric.formula_version for metric in metrics.values())


def test_cutoff_never_uses_facts_filed_later() -> None:
    cutoff = datetime(2023, 10, 1, tzinfo=UTC)
    metrics = derive_evidence(observations(), cutoff)

    source_ids = {
        source_id for metric in metrics for source_id in metric.source_observation_ids
    }
    assert source_ids
    assert not any("23-000106" in source_id for source_id in source_ids)
    revenue_growth = next(metric for metric in metrics if metric.name == "revenue_growth")
    assert revenue_growth.value == Decimal("0.077938")


def test_records_unavailable_reason_instead_of_substituting_current_data() -> None:
    cutoff = datetime(2021, 1, 1, tzinfo=UTC)
    metrics = derive_evidence(observations(), cutoff, price=Decimal("130"))

    assert all(metric.value is None for metric in metrics)
    assert all(metric.unavailable_reason for metric in metrics)
    assert all(metric.source_observation_ids == () for metric in metrics)
