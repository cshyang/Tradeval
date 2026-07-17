"""Tests for provider-neutral, point-in-time daily price contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast

import pytest

from retailtrader.data.protocol import (
    AvailableMarketBar,
    PriceBatch,
    PriceQuery,
    canonical_json,
    canonical_normalized_rows,
    normalized_rows_hash,
    query_key,
    validate_batch_identity,
)
from retailtrader.domain import MarketBar

RAW_HASH = "a" * 64
RETRIEVED_AT = datetime(2024, 1, 4, 22, tzinfo=UTC)


def _observation(
    symbol: str = "AAPL",
    session: date = date(2024, 1, 3),
    *,
    open_available_at: datetime | None = None,
    close_available_at: datetime | None = None,
    source_ref: str = "provider:AAPL:2024-01-03",
) -> AvailableMarketBar:
    return AvailableMarketBar(
        bar=MarketBar(
            symbol=symbol,
            session=session,
            open=Decimal("184.22"),
            high=Decimal("185.88"),
            low=Decimal("183.43"),
            close=Decimal("184.25"),
            volume=58_412_300,
        ),
        open_available_at=open_available_at
        or datetime.combine(session, datetime.min.time(), tzinfo=UTC).replace(hour=14, minute=30),
        close_available_at=close_available_at
        or datetime.combine(session, datetime.min.time(), tzinfo=UTC).replace(hour=21),
        source_ref=source_ref,
    )


def _batch(
    observations: tuple[AvailableMarketBar, ...] | None = None,
    *,
    query: PriceQuery | None = None,
    retrieved_at: datetime = RETRIEVED_AT,
    transport: str = "openbb",
    provider: str = "yfinance",
    raw_hash: str = RAW_HASH,
    provider_versions: tuple[tuple[str, str], ...] = (
        ("openbb", "4.2.0"),
        ("openbb-yfinance", "1.4.0"),
    ),
) -> PriceBatch:
    rows = observations if observations is not None else (_observation(),)
    price_query = query or PriceQuery(("AAPL",), date(2024, 1, 2), date(2024, 1, 4))
    return PriceBatch.create(
        transport=transport,
        provider=provider,
        query=price_query,
        observations=rows,
        retrieved_at=retrieved_at,
        raw_hash=raw_hash,
        provider_versions=provider_versions,
    )


def test_price_query_normalizes_symbols_and_validates_range() -> None:
    query = PriceQuery((" msft ", "aapl", "MSFT"), date(2024, 1, 1), date(2024, 1, 5))
    sequence_query = PriceQuery(
        cast(Any, [" msft ", "aapl", "MSFT"]),
        date(2024, 1, 1),
        date(2024, 1, 5),
    )

    assert query.symbols == ("AAPL", "MSFT")
    assert sequence_query.symbols == query.symbols
    assert query.interval == "1d"
    assert query.adjustment == "splits_and_dividends"

    with pytest.raises(ValueError, match="at least one symbol"):
        PriceQuery((), date(2024, 1, 1), date(2024, 1, 5))
    with pytest.raises(ValueError, match="symbol must be nonempty"):
        PriceQuery((" ",), date(2024, 1, 1), date(2024, 1, 5))
    with pytest.raises(ValueError, match="start must not be after end"):
        PriceQuery(("AAPL",), date(2024, 1, 6), date(2024, 1, 5))


def test_price_query_rejects_invalid_symbol_runtime_types() -> None:
    with pytest.raises(TypeError, match="non-string sequence of strings"):
        PriceQuery(cast(Any, "AAPL"), date(2024, 1, 1), date(2024, 1, 5))
    with pytest.raises(TypeError, match="contain only strings"):
        PriceQuery(cast(Any, ("AAPL", 42)), date(2024, 1, 1), date(2024, 1, 5))


@pytest.mark.parametrize(
    ("start", "end", "invalid_field"),
    [
        ("2024-01-01", date(2024, 1, 5), "start"),
        (datetime(2024, 1, 1), date(2024, 1, 5), "start"),
        (date(2024, 1, 1), "2024-01-05", "end"),
        (date(2024, 1, 1), datetime(2024, 1, 5), "end"),
    ],
)
def test_price_query_rejects_string_and_datetime_dates(
    start: Any,
    end: Any,
    invalid_field: str,
) -> None:
    with pytest.raises(TypeError, match=rf"query {invalid_field} must be a date"):
        PriceQuery(("AAPL",), start, end)


def test_price_query_rejects_unsupported_interval_and_adjustment() -> None:
    with pytest.raises(ValueError, match="unsupported query interval '1h'"):
        PriceQuery(
            ("AAPL",),
            date(2024, 1, 1),
            date(2024, 1, 5),
            interval=cast(Any, "1h"),
        )
    with pytest.raises(ValueError, match="unsupported query adjustment 'unadjusted'"):
        PriceQuery(
            ("AAPL",),
            date(2024, 1, 1),
            date(2024, 1, 5),
            adjustment=cast(Any, "unadjusted"),
        )


def test_available_bar_requires_utc_normalizable_ordered_session_availability() -> None:
    eastern = timezone(timedelta(hours=-5))
    observation = _observation(
        open_available_at=datetime(2024, 1, 3, 9, 30, tzinfo=eastern),
        close_available_at=datetime(2024, 1, 3, 16, tzinfo=eastern),
    )

    assert observation.open_available_at == datetime(2024, 1, 3, 14, 30, tzinfo=UTC)
    assert observation.close_available_at == datetime(2024, 1, 3, 21, tzinfo=UTC)

    with pytest.raises(ValueError, match="timezone-aware"):
        _observation(open_available_at=datetime(2024, 1, 3, 14, 30))
    with pytest.raises(ValueError, match="before close"):
        _observation(
            open_available_at=datetime(2024, 1, 3, 21, tzinfo=UTC),
            close_available_at=datetime(2024, 1, 3, 21, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="canonical session open"):
        _observation(open_available_at=datetime(2024, 1, 3, 14, 29, tzinfo=UTC))
    with pytest.raises(ValueError, match="canonical session close"):
        _observation(close_available_at=datetime(2024, 1, 3, 20, 59, tzinfo=UTC))
    with pytest.raises(ValueError, match="source_ref must be nonempty"):
        _observation(source_ref=" ")


def test_batch_normalizes_row_order_and_retrieval_timezone() -> None:
    msft = _observation("MSFT", date(2024, 1, 4), source_ref="msft")
    aapl_later = _observation("AAPL", date(2024, 1, 4), source_ref="aapl-later")
    aapl_earlier = _observation("AAPL", date(2024, 1, 3), source_ref="aapl-earlier")
    query = PriceQuery(("MSFT", "AAPL"), date(2024, 1, 2), date(2024, 1, 4))
    eastern = timezone(timedelta(hours=-5))

    batch = _batch(
        (msft, aapl_later, aapl_earlier),
        query=query,
        retrieved_at=datetime(2024, 1, 4, 17, tzinfo=eastern),
    )

    assert [(row.bar.session, row.bar.symbol) for row in batch.observations] == [
        (date(2024, 1, 3), "AAPL"),
        (date(2024, 1, 4), "AAPL"),
        (date(2024, 1, 4), "MSFT"),
    ]
    assert batch.retrieved_at == RETRIEVED_AT
    assert batch.normalized_hash == normalized_rows_hash(batch.observations)


def test_batch_rejects_duplicates_rows_outside_query_and_future_availability() -> None:
    row = _observation()
    with pytest.raises(ValueError, match="duplicate observation"):
        _batch((row, row))

    with pytest.raises(ValueError, match="not requested"):
        _batch((_observation("MSFT"),))

    with pytest.raises(ValueError, match="outside query range"):
        _batch((_observation(session=date(2024, 1, 1)),))

    with pytest.raises(ValueError, match="after retrieval"):
        _batch(
            (
                _observation(
                    close_available_at=datetime(2024, 1, 5, 21, tzinfo=UTC),
                ),
            )
        )


def test_batch_rejects_bad_identity_timestamps_versions_and_hashes() -> None:
    with pytest.raises(ValueError, match="transport must be nonempty"):
        _batch(transport=" ")
    with pytest.raises(ValueError, match="provider must be nonempty"):
        _batch(provider="")
    with pytest.raises(ValueError, match="timezone-aware"):
        _batch(retrieved_at=datetime(2024, 1, 4, 22))
    with pytest.raises(ValueError, match="sorted and unique"):
        _batch(provider_versions=(("z", "1"), ("a", "1")))
    with pytest.raises(ValueError, match="tuple of pairs"):
        _batch(provider_versions=cast(Any, [["openbb", "4.2.0"]]))
    with pytest.raises(ValueError, match="raw_hash must be a lowercase SHA-256"):
        _batch(raw_hash="not-a-hash")

    valid = _batch()
    with pytest.raises(ValueError, match="normalized_hash does not match"):
        replace(valid, normalized_hash="b" * 64)


def test_canonical_rows_json_and_hash_are_stable() -> None:
    aapl = _observation()
    msft = _observation("MSFT", source_ref="provider:MSFT:2024-01-03")

    rows = canonical_normalized_rows((msft, aapl))
    assert rows == canonical_normalized_rows((aapl, msft))
    document = canonical_json(rows)
    assert json.loads(document)[0] == {
        "close": "184.25",
        "close_available_at": "2024-01-03T21:00:00Z",
        "high": "185.88",
        "low": "183.43",
        "open": "184.22",
        "open_available_at": "2024-01-03T14:30:00Z",
        "session": "2024-01-03",
        "source_ref": "provider:AAPL:2024-01-03",
        "symbol": "AAPL",
        "volume": 58_412_300,
    }
    assert normalized_rows_hash((msft, aapl)) == hashlib.sha256(document.encode()).hexdigest()


def test_query_key_is_stable_sha256_and_covers_valid_identity_dimensions() -> None:
    base = PriceQuery(("AAPL",), date(2024, 1, 1), date(2024, 1, 5))
    base_key = query_key("openbb", "yfinance", base)

    assert len(base_key) == 64
    assert base_key == query_key("openbb", "yfinance", base)
    variants = [
        ("other", "yfinance", base),
        ("openbb", "other", base),
        ("openbb", "yfinance", replace(base, symbols=("MSFT",))),
        ("openbb", "yfinance", replace(base, start=date(2024, 1, 2))),
        ("openbb", "yfinance", replace(base, end=date(2024, 1, 6))),
    ]
    assert all(query_key(*variant) != base_key for variant in variants)


def test_batch_identity_helper_rejects_source_mismatch() -> None:
    batch = _batch()

    validate_batch_identity(batch, transport="openbb", provider="yfinance")
    with pytest.raises(ValueError, match="batch identity mismatch"):
        validate_batch_identity(batch, transport="other", provider="yfinance")
