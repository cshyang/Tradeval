"""Immutable normalized market-price cache tests."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import retailtrader.data.cache as cache_module
from retailtrader.data.cache import (
    CachedDailyPriceSource,
    PriceCache,
    PriceCacheIntegrityError,
)
from retailtrader.data.protocol import (
    AvailableMarketBar,
    PriceBatch,
    PriceQuery,
)
from retailtrader.domain import MarketBar

NY = ZoneInfo("America/New_York")
QUERY = PriceQuery(("MSFT", "AAPL"), date(2025, 1, 2), date(2025, 1, 2))


def _observation(symbol: str, close: str = "101.25") -> AvailableMarketBar:
    session = date(2025, 1, 2)
    return AvailableMarketBar(
        bar=MarketBar(
            symbol=symbol,
            session=session,
            open=Decimal("100.10"),
            high=Decimal("102.00"),
            low=Decimal("99.50"),
            close=Decimal(close),
            volume=1_234_567,
        ),
        open_available_at=datetime(2025, 1, 2, 9, 30, tzinfo=NY),
        close_available_at=datetime(2025, 1, 2, 16, 0, tzinfo=NY),
        source_ref=f"openbb:yfinance:{symbol}:2025-01-02",
    )


def _batch(*, close: str = "101.25", raw: str = "a" * 64) -> PriceBatch:
    return PriceBatch.create(
        transport="openbb",
        provider="yfinance",
        query=QUERY,
        observations=(
            _observation("MSFT", close),
            _observation("AAPL", close),
        ),
        retrieved_at=datetime(2025, 1, 3, 12, tzinfo=UTC),
        raw_hash=raw,
        provider_versions=(("openbb", "4.7.2"), ("openbb-yfinance", "1.6.3")),
    )


class FakeSource:
    transport = "openbb"
    provider = "yfinance"

    def __init__(self, batch: PriceBatch) -> None:
        self.batch = batch
        self.calls = 0

    def fetch(self, query: PriceQuery) -> PriceBatch:
        self.calls += 1
        return self.batch


def test_cache_miss_and_exact_round_trip(tmp_path: Path) -> None:
    cache = PriceCache(tmp_path)
    batch = _batch()

    assert cache.load("openbb", "yfinance", QUERY) is None
    cache.store(batch)
    loaded = cache.load("openbb", "yfinance", QUERY)

    assert loaded == batch
    assert loaded is not None
    assert loaded.observations[0].bar.open == Decimal("100.10")
    assert loaded.observations[0].open_available_at.tzinfo == UTC
    entry = cache.entry_path("openbb", "yfinance", QUERY)
    assert {path.name for path in entry.iterdir()} == {"data.parquet", "metadata.json"}
    metadata = json.loads((entry / "metadata.json").read_text())
    assert metadata["symbols_complete"] is True
    assert metadata["actual_symbols"] == ["AAPL", "MSFT"]
    assert metadata["first_session"] == metadata["last_session"] == "2025-01-02"


def test_cache_hit_does_not_call_source(tmp_path: Path) -> None:
    batch = _batch()
    source = FakeSource(batch)
    loader = CachedDailyPriceSource(source, PriceCache(tmp_path))

    first = loader.fetch(QUERY)
    second = loader.fetch(QUERY)

    assert first.cache_status == "miss"
    assert second.cache_status == "hit"
    assert first.batch == second.batch == batch
    assert source.calls == 1


def test_cache_isolates_transport_provider_and_query(tmp_path: Path) -> None:
    cache = PriceCache(tmp_path)
    cache.store(_batch())
    other_query = PriceQuery(("AAPL", "MSFT"), date(2025, 1, 2), date(2025, 1, 3))

    assert cache.load("direct", "yfinance", QUERY) is None
    assert cache.load("openbb", "other", QUERY) is None
    assert cache.load("openbb", "yfinance", other_query) is None


def test_cache_rejects_source_identity_mismatch(tmp_path: Path) -> None:
    source = FakeSource(_batch())
    source.provider = "other"
    loader = CachedDailyPriceSource(source, PriceCache(tmp_path))

    with pytest.raises(ValueError, match="identity mismatch"):
        loader.fetch(QUERY)


def test_cache_rejects_corrupt_metadata_and_parquet(tmp_path: Path) -> None:
    cache = PriceCache(tmp_path)
    cache.store(_batch())
    entry = cache.entry_path("openbb", "yfinance", QUERY)
    metadata_path = entry / "metadata.json"
    original_metadata = metadata_path.read_bytes()

    metadata = json.loads(original_metadata)
    metadata["normalized_hash"] = "not-a-hash"
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(PriceCacheIntegrityError, match="normalized_hash"):
        cache.load("openbb", "yfinance", QUERY)

    metadata_path.write_bytes(original_metadata)
    (entry / "data.parquet").write_bytes(b"corrupt")
    with pytest.raises(PriceCacheIntegrityError, match="Parquet hash"):
        cache.load("openbb", "yfinance", QUERY)


def test_cache_rejects_conflicting_immutable_entry(tmp_path: Path) -> None:
    cache = PriceCache(tmp_path)
    cache.store(_batch())

    with pytest.raises(PriceCacheIntegrityError, match="conflicting immutable"):
        cache.store(_batch(close="109.00", raw="b" * 64))


def test_failed_prepublication_write_cleans_temporary_directory(tmp_path: Path) -> None:
    def fail(point: str) -> None:
        if point == "before_publish":
            raise OSError("injected")

    cache = PriceCache(tmp_path, failure_hook=fail)
    with pytest.raises(OSError, match="injected"):
        cache.store(_batch())

    parent = tmp_path / "openbb" / "yfinance"
    assert not cache.entry_path("openbb", "yfinance", QUERY).exists()
    assert list(parent.glob(".*")) == []


def test_postpublication_failure_recovers_complete_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(point: str) -> None:
        if point == "after_publish":
            raise OSError("injected")

    cache = PriceCache(tmp_path, failure_hook=fail)
    with pytest.raises(OSError, match="injected"):
        cache.store(_batch())

    synced: list[Path] = []
    real_fsync_directory = cache_module._fsync_directory

    def record_fsync(path: Path) -> None:
        synced.append(path)
        real_fsync_directory(path)

    monkeypatch.setattr(cache_module, "_fsync_directory", record_fsync)
    assert PriceCache(tmp_path).load("openbb", "yfinance", QUERY) == _batch()
    assert cache.entry_path("openbb", "yfinance", QUERY).parent in synced


def test_concurrent_identical_writers_are_idempotent(tmp_path: Path) -> None:
    batch = _batch()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: PriceCache(tmp_path).store(batch), range(2)))

    assert results == [None, None]
    assert PriceCache(tmp_path).load("openbb", "yfinance", QUERY) == batch


def test_concurrent_conflicting_writers_cannot_overwrite(tmp_path: Path) -> None:
    batches = [_batch(), _batch(close="109.00", raw="b" * 64)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(PriceCache(tmp_path).store, batch) for batch in batches]
    errors = [future.exception() for future in futures]

    assert sum(error is None for error in errors) == 1
    assert sum(isinstance(error, PriceCacheIntegrityError) for error in errors) == 1
    loaded = PriceCache(tmp_path).load("openbb", "yfinance", QUERY)
    assert loaded in batches


@pytest.mark.parametrize("transport,provider", [("../openbb", "yfinance"), ("openbb", "../x")])
def test_cache_rejects_path_traversal(
    tmp_path: Path, transport: str, provider: str
) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        PriceCache(tmp_path).load(transport, provider, QUERY)
