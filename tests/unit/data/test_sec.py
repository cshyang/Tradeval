from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from retailtrader.data.fundamental_cache import FundamentalCache
from retailtrader.data.sec import (
    LARGE_CAP_CIKS,
    SecCompanyFacts,
    SecCompanyFactsClient,
    SecDataError,
    normalize_company_facts,
)

FIXTURE = Path(__file__).parents[2] / "fixtures/market_data/sec_companyfacts_aapl.json"


def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def document() -> SecCompanyFacts:
    return SecCompanyFacts.create(
        cik="0000320193",
        payload=payload(),
        retrieved_at=datetime(2024, 2, 1, tzinfo=UTC),
    )


def test_normalizes_required_facts_with_conservative_availability() -> None:
    observations = normalize_company_facts(payload())

    assert {item.metric for item in observations} == {
        "revenue",
        "net_income",
        "operating_cash_flow",
        "capital_expenditure",
        "assets",
        "liabilities",
        "debt",
        "diluted_shares",
    }
    revenue_2023 = next(
        item for item in observations if item.metric == "revenue" and item.fiscal_year == 2023
    )
    assert revenue_2023.available_at == datetime(2023, 11, 4, tzinfo=UTC)
    assert revenue_2023.source_ref == "sec:companyfacts:0000320193:0000320193-23-000106"


def test_rejects_selected_fact_without_filing_date() -> None:
    broken = payload()
    revenue = broken["facts"]["us-gaap"][
        "RevenueFromContractWithCustomerExcludingAssessedTax"
    ]["units"]["USD"][0]
    del revenue["filed"]

    with pytest.raises(SecDataError, match="filed"):
        normalize_company_facts(broken)


def test_companyfacts_document_hashes_raw_and_normalized_content() -> None:
    facts = document()

    assert len(facts.raw_hash) == 64
    assert len(facts.normalized_hash) == 64
    assert len(facts.observations) == 24
    assert facts.cik == "0000320193"


def test_first_universe_has_30_unique_sec_identifiers() -> None:
    assert len(LARGE_CAP_CIKS) == 30
    assert len(set(LARGE_CAP_CIKS.values())) == 30
    assert all(len(cik) == 10 and cik.isdigit() for cik in LARGE_CAP_CIKS.values())


def test_fundamental_cache_round_trips_and_rejects_conflicts(tmp_path: Path) -> None:
    cache = FundamentalCache(tmp_path)
    facts = document()

    assert cache.load(facts.cik) is None
    cache.store(facts)
    assert cache.load(facts.cik) == facts

    changed = SecCompanyFacts.create(
        cik=facts.cik,
        payload=payload() | {"entityName": "Changed"},
        retrieved_at=facts.retrieved_at,
    )
    with pytest.raises(SecDataError, match="conflicting immutable"):
        cache.store(changed)


def test_fundamental_cache_cleans_failed_prepublication_write(tmp_path: Path) -> None:
    def fail(point: str) -> None:
        if point == "before_publish":
            raise OSError("injected")

    cache = FundamentalCache(tmp_path, failure_hook=fail)
    with pytest.raises(OSError, match="injected"):
        cache.store(document())

    target = cache.entry_path("0000320193")
    assert not target.exists()
    assert list(target.parent.glob(f".{target.name}.*")) == []


def test_fundamental_cache_serializes_concurrent_identical_writers(tmp_path: Path) -> None:
    facts = document()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _: FundamentalCache(tmp_path).store(facts), range(2))
        )

    assert results == [None, None]
    assert FundamentalCache(tmp_path).load(facts.cik) == facts


def test_sec_client_requires_identification_and_sends_it() -> None:
    with pytest.raises(ValueError, match="contact email"):
        SecCompanyFactsClient(user_agent="anonymous")

    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self) -> bytes:
            return json.dumps(payload()).encode()

    def opener(request, *, timeout):
        requests.append((request, timeout))
        return Response()

    client = SecCompanyFactsClient(
        user_agent="AgentTrader research@example.com",
        opener=opener,
        clock=lambda: datetime(2024, 2, 1, tzinfo=UTC),
    )

    facts = client.fetch("320193")

    assert facts.cik == "0000320193"
    assert requests[0][0].get_header("User-agent") == "AgentTrader research@example.com"
    assert requests[0][1] == 30
