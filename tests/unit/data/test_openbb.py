"""Offline contract tests for the optional OpenBB/Yahoo adapter."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from retailtrader.data.openbb import (
    OpenBBDataError,
    OpenBBUnavailableError,
    OpenBBYFinancePriceSource,
)
from retailtrader.data.protocol import PriceQuery

FIXTURE = (
    Path(__file__).parents[2]
    / "fixtures/market_data/openbb_yfinance_aapl_daily.json"
)
QUERY = PriceQuery(("AAPL",), date(2025, 1, 2), date(2025, 1, 3))
RETRIEVED_AT = datetime(2025, 1, 7, 12, tzinfo=UTC)


@dataclass
class FakeResponse:
    provider: str
    results: list[dict[str, Any]]


class FakeHistorical:
    def __init__(self, rows_by_symbol: dict[str, list[dict[str, Any]]]) -> None:
        self.rows_by_symbol = rows_by_symbol
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        return FakeResponse("yfinance", deepcopy(self.rows_by_symbol[kwargs["symbol"]]))


def _fixture_rows() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text())["results"]


def _source(historical: Any) -> OpenBBYFinancePriceSource:
    versions = {"openbb": "4.7.2", "openbb-yfinance": "1.6.3"}
    return OpenBBYFinancePriceSource(
        historical,
        clock=lambda: RETRIEVED_AT,
        version_lookup=versions.__getitem__,
    )


def test_fetch_uses_explicit_provider_arguments_and_filters_inclusive_range() -> None:
    historical = FakeHistorical({"AAPL": _fixture_rows()})
    batch = _source(historical).fetch(QUERY)

    assert historical.calls == [
        {
            "symbol": "AAPL",
            "provider": "yfinance",
            "interval": "1d",
            "adjustment": "splits_and_dividends",
            "extended_hours": False,
            "start_date": date(2025, 1, 2),
            "end_date": date(2025, 1, 4),
        }
    ]
    assert batch.transport == "openbb"
    assert batch.provider == "yfinance"
    assert batch.provider_versions == (("openbb", "4.7.2"), ("openbb-yfinance", "1.6.3"))
    assert [item.bar.session for item in batch.observations] == [
        date(2025, 1, 2),
        date(2025, 1, 3),
    ]
    first = batch.observations[0]
    assert str(first.bar.open) == "248.93"
    assert first.bar.volume == 55_740_700
    assert first.open_available_at == datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    assert first.close_available_at == datetime(2025, 1, 2, 21, 0, tzinfo=UTC)
    assert first.source_ref == (
        "openbb:equity.price.historical:yfinance:AAPL:2025-01-02"
    )


def test_raw_and_normalized_hashes_are_stable() -> None:
    first = _source(FakeHistorical({"AAPL": _fixture_rows()})).fetch(QUERY)
    second = _source(FakeHistorical({"AAPL": _fixture_rows()})).fetch(QUERY)
    changed_rows = _fixture_rows()
    changed_rows[0]["close"] = 244.00
    changed = _source(FakeHistorical({"AAPL": changed_rows})).fetch(QUERY)

    assert first.raw_hash == second.raw_hash
    assert first.normalized_hash == second.normalized_hash
    assert first.raw_hash != changed.raw_hash
    assert first.normalized_hash != changed.normalized_hash


def test_each_symbol_is_fetched_separately_and_retains_identity() -> None:
    rows = _fixture_rows()[:1]
    historical = FakeHistorical({"AAPL": rows, "MSFT": rows})
    query = PriceQuery(("MSFT", "AAPL"), date(2025, 1, 2), date(2025, 1, 2))

    batch = _source(historical).fetch(query)

    assert [call["symbol"] for call in historical.calls] == ["AAPL", "MSFT"]
    assert {(item.bar.symbol, item.bar.session) for item in batch.observations} == {
        ("AAPL", date(2025, 1, 2)),
        ("MSFT", date(2025, 1, 2)),
    }


def test_missing_optional_dependency_has_actionable_error() -> None:
    def missing(_: str) -> Any:
        raise ModuleNotFoundError("openbb")

    source = OpenBBYFinancePriceSource(
        importer=missing,
        clock=lambda: RETRIEVED_AT,
    )
    with pytest.raises(OpenBBUnavailableError, match="uv sync --extra data-openbb"):
        source.fetch(QUERY)


def test_provider_mismatch_is_rejected() -> None:
    def historical(**_: Any) -> FakeResponse:
        return FakeResponse("other", _fixture_rows())

    with pytest.raises(OpenBBDataError, match="provider mismatch"):
        _source(historical).fetch(QUERY)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("open", None, "open"),
        ("high", float("nan"), "high"),
        ("low", -1, "low"),
        ("close", float("inf"), "close"),
        ("volume", 1.5, "volume"),
    ],
)
def test_invalid_required_values_are_rejected(
    field: str, value: Any, message: str
) -> None:
    rows = _fixture_rows()
    rows[0][field] = value
    with pytest.raises(OpenBBDataError, match=message):
        _source(FakeHistorical({"AAPL": rows})).fetch(QUERY)


def test_invalid_ohlc_relationship_is_rejected() -> None:
    rows = _fixture_rows()
    rows[0]["high"] = 200
    with pytest.raises(OpenBBDataError, match="OHLC"):
        _source(FakeHistorical({"AAPL": rows})).fetch(QUERY)


def test_duplicate_and_unfinished_rows_are_rejected() -> None:
    duplicate = _fixture_rows()
    duplicate.insert(1, deepcopy(duplicate[0]))
    with pytest.raises(OpenBBDataError, match="duplicate"):
        _source(FakeHistorical({"AAPL": duplicate})).fetch(QUERY)

    unfinished = _fixture_rows()
    unfinished[0]["intra_period"] = True
    with pytest.raises(OpenBBDataError, match="unfinished"):
        _source(FakeHistorical({"AAPL": unfinished})).fetch(QUERY)


def test_row_not_complete_at_retrieval_is_rejected() -> None:
    source = OpenBBYFinancePriceSource(
        FakeHistorical({"AAPL": _fixture_rows()}),
        clock=lambda: datetime(2025, 1, 2, 20, tzinfo=UTC),
        version_lookup=lambda _: "1.0",
    )
    with pytest.raises(OpenBBDataError, match="not complete"):
        source.fetch(QUERY)


def test_symbol_without_completed_rows_is_rejected() -> None:
    query = PriceQuery(("AAPL",), date(2025, 2, 1), date(2025, 2, 2))
    with pytest.raises(OpenBBDataError, match="no completed rows returned for: AAPL"):
        _source(FakeHistorical({"AAPL": _fixture_rows()})).fetch(query)


def test_transport_failure_is_wrapped_with_symbol() -> None:
    def historical(**_: Any) -> Any:
        raise TimeoutError("upstream timeout")

    with pytest.raises(OpenBBDataError, match="request failed for AAPL"):
        _source(historical).fetch(QUERY)


def test_query_end_is_extended_exactly_one_calendar_day() -> None:
    historical = FakeHistorical({"AAPL": _fixture_rows()})
    _source(historical).fetch(QUERY)
    assert historical.calls[0]["end_date"] == QUERY.end + timedelta(days=1)
