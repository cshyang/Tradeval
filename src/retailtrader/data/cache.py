"""Immutable, integrity-checked cache for normalized daily market prices."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

import pandas as pd

from retailtrader.data.protocol import (
    AvailableMarketBar,
    DailyPriceSource,
    PriceBatch,
    PriceQuery,
    canonical_json,
    query_key,
    validate_batch_identity,
)
from retailtrader.domain import MarketBar

CACHE_SCHEMA_VERSION = 1
_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_COLUMNS = (
    "symbol",
    "session",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_available_at",
    "close_available_at",
    "source_ref",
)
FailureHook = Callable[[str], None]


class PriceCacheIntegrityError(RuntimeError):
    """A cache entry is corrupt or conflicts with immutable cached content."""


def _safe_component(value: str, name: str) -> str:
    if not isinstance(value, str) or _COMPONENT.fullmatch(value) is None:
        raise ValueError(f"unsafe {name} cache path component: {value!r}")
    if value in {".", ".."}:
        raise ValueError(f"unsafe {name} cache path component: {value!r}")
    return value


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _query_payload(query: PriceQuery) -> dict[str, object]:
    return {
        "symbols": list(query.symbols),
        "start": query.start.isoformat(),
        "end": query.end.isoformat(),
        "interval": query.interval,
        "adjustment": query.adjustment,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _batch_rows(batch: PriceBatch) -> list[dict[str, object]]:
    return [
        {
            "symbol": observation.bar.symbol,
            "session": observation.bar.session.isoformat(),
            "open": str(observation.bar.open),
            "high": str(observation.bar.high),
            "low": str(observation.bar.low),
            "close": str(observation.bar.close),
            "volume": observation.bar.volume,
            "open_available_at": observation.open_available_at.isoformat(),
            "close_available_at": observation.close_available_at.isoformat(),
            "source_ref": observation.source_ref,
        }
        for observation in batch.observations
    ]


def _metadata(batch: PriceBatch, parquet_hash: str) -> dict[str, object]:
    sessions = [item.bar.session for item in batch.observations]
    actual_symbols = sorted({item.bar.symbol for item in batch.observations})
    return {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "transport": batch.transport,
        "provider": batch.provider,
        "provider_versions": [list(item) for item in batch.provider_versions],
        "query": _query_payload(batch.query),
        "query_hash": query_key(batch.transport, batch.provider, batch.query),
        "retrieved_at": batch.retrieved_at.isoformat(),
        "adjustment": batch.query.adjustment,
        "source_refs": sorted({item.source_ref for item in batch.observations}),
        "raw_hash": batch.raw_hash,
        "normalized_hash": batch.normalized_hash,
        "parquet_hash": parquet_hash,
        "requested_symbols": list(batch.query.symbols),
        "actual_symbols": actual_symbols,
        "symbols_complete": actual_symbols == list(batch.query.symbols),
        "row_count": len(batch.observations),
        "first_session": min(sessions).isoformat() if sessions else None,
        "last_session": max(sessions).isoformat() if sessions else None,
    }


@dataclass(frozen=True)
class PriceFetchResult:
    batch: PriceBatch
    cache_status: Literal["hit", "miss", "bypass"]

    def __post_init__(self) -> None:
        if not isinstance(self.batch, PriceBatch):
            raise TypeError("batch must be a PriceBatch")
        if self.cache_status not in {"hit", "miss", "bypass"}:
            raise ValueError(f"unsupported cache status: {self.cache_status!r}")


@runtime_checkable
class DailyPriceLoader(Protocol):
    """Price loader that reports whether normalized data came from cache."""

    def fetch(self, query: PriceQuery) -> PriceFetchResult: ...


class PriceCache:
    """Store complete immutable cache entries, keyed by source and query."""

    def __init__(self, root: Path, failure_hook: FailureHook | None = None) -> None:
        self.root = Path(root)
        self.failure_hook = failure_hook

    def _fail(self, point: str) -> None:
        if self.failure_hook is not None:
            self.failure_hook(point)

    def entry_path(self, transport: str, provider: str, query: PriceQuery) -> Path:
        transport = _safe_component(transport, "transport")
        provider = _safe_component(provider, "provider")
        return self.root / transport / provider / query_key(transport, provider, query)

    def load(
        self, transport: str, provider: str, query: PriceQuery
    ) -> PriceBatch | None:
        entry = self.entry_path(transport, provider, query)
        if not entry.exists():
            return None
        # A prior process may have crashed after publishing the complete entry
        # but before syncing the parent directory. Make the visible entry durable
        # before treating it as a cache hit.
        _fsync_directory(entry.parent)
        return self._load_entry(entry, transport, provider, query)

    def _load_entry(
        self, entry: Path, transport: str, provider: str, query: PriceQuery
    ) -> PriceBatch:
        try:
            if not entry.is_dir():
                raise PriceCacheIntegrityError(f"cache entry is not a directory: {entry}")
            parquet_path = entry / "data.parquet"
            metadata_path = entry / "metadata.json"
            if not parquet_path.is_file() or not metadata_path.is_file():
                raise PriceCacheIntegrityError(f"incomplete cache entry: {entry}")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self._validate_metadata(metadata, transport, provider, query)
            if _sha256_file(parquet_path) != metadata["parquet_hash"]:
                raise PriceCacheIntegrityError("cached Parquet hash mismatch")
            frame = pd.read_parquet(parquet_path)
            if tuple(frame.columns) != _COLUMNS:
                raise PriceCacheIntegrityError("cached Parquet columns do not match schema")
            if len(frame) != metadata["row_count"]:
                raise PriceCacheIntegrityError("cached row count does not match metadata")
            observations = tuple(self._observation_from_row(row) for row in frame.to_dict("records"))
            batch = PriceBatch(
                transport=metadata["transport"],
                provider=metadata["provider"],
                query=query,
                observations=observations,
                retrieved_at=datetime.fromisoformat(metadata["retrieved_at"]),
                raw_hash=metadata["raw_hash"],
                normalized_hash=metadata["normalized_hash"],
                provider_versions=tuple(tuple(item) for item in metadata["provider_versions"]),
            )
            self._validate_loaded_summary(batch, metadata)
            return batch
        except PriceCacheIntegrityError:
            raise
        except Exception as exc:  # cache boundary: normalize parser/provider errors
            raise PriceCacheIntegrityError(f"invalid cache entry {entry}: {exc}") from exc

    @staticmethod
    def _observation_from_row(row: dict[str, object]) -> AvailableMarketBar:
        if any(pd.isna(row[column]) for column in _COLUMNS):
            raise PriceCacheIntegrityError("cached Parquet contains null values")
        volume = row["volume"]
        if isinstance(volume, bool) or int(volume) != volume:
            raise PriceCacheIntegrityError("cached volume must be an integer")
        bar = MarketBar(
            symbol=str(row["symbol"]),
            session=date.fromisoformat(str(row["session"])),
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=int(volume),
        )
        return AvailableMarketBar(
            bar=bar,
            open_available_at=datetime.fromisoformat(str(row["open_available_at"])),
            close_available_at=datetime.fromisoformat(str(row["close_available_at"])),
            source_ref=str(row["source_ref"]),
        )

    @staticmethod
    def _validate_metadata(
        metadata: object, transport: str, provider: str, query: PriceQuery
    ) -> None:
        if not isinstance(metadata, dict):
            raise PriceCacheIntegrityError("cache metadata must be an object")
        required = {
            "cache_schema_version",
            "transport",
            "provider",
            "provider_versions",
            "query",
            "query_hash",
            "retrieved_at",
            "adjustment",
            "source_refs",
            "raw_hash",
            "normalized_hash",
            "parquet_hash",
            "requested_symbols",
            "actual_symbols",
            "symbols_complete",
            "row_count",
            "first_session",
            "last_session",
        }
        if set(metadata) != required:
            raise PriceCacheIntegrityError("cache metadata fields do not match schema")
        expected_query = _query_payload(query)
        expected_hash = query_key(transport, provider, query)
        if metadata["cache_schema_version"] != CACHE_SCHEMA_VERSION:
            raise PriceCacheIntegrityError("unsupported cache schema version")
        if metadata["transport"] != transport or metadata["provider"] != provider:
            raise PriceCacheIntegrityError("cache source identity mismatch")
        if metadata["query"] != expected_query or metadata["query_hash"] != expected_hash:
            raise PriceCacheIntegrityError("cache query identity mismatch")
        if metadata["adjustment"] != query.adjustment:
            raise PriceCacheIntegrityError("cache adjustment mismatch")
        if metadata["requested_symbols"] != list(query.symbols):
            raise PriceCacheIntegrityError("cache requested symbols mismatch")
        if metadata["symbols_complete"] is not True:
            raise PriceCacheIntegrityError("cached response is symbol-incomplete")
        if not isinstance(metadata["row_count"], int) or metadata["row_count"] <= 0:
            raise PriceCacheIntegrityError("cache must contain at least one row")
        for name in ("raw_hash", "normalized_hash", "parquet_hash", "query_hash"):
            value = metadata[name]
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise PriceCacheIntegrityError(f"invalid {name}")

    @staticmethod
    def _validate_loaded_summary(batch: PriceBatch, metadata: dict[str, object]) -> None:
        actual_symbols = sorted({item.bar.symbol for item in batch.observations})
        sessions = [item.bar.session for item in batch.observations]
        if actual_symbols != metadata["actual_symbols"]:
            raise PriceCacheIntegrityError("cached actual symbols mismatch")
        if actual_symbols != list(batch.query.symbols):
            raise PriceCacheIntegrityError("cached response does not cover every symbol")
        if sorted({item.source_ref for item in batch.observations}) != metadata["source_refs"]:
            raise PriceCacheIntegrityError("cached source references mismatch")
        if min(sessions).isoformat() != metadata["first_session"]:
            raise PriceCacheIntegrityError("cached first session mismatch")
        if max(sessions).isoformat() != metadata["last_session"]:
            raise PriceCacheIntegrityError("cached last session mismatch")

    def store(self, batch: PriceBatch) -> None:
        if not isinstance(batch, PriceBatch):
            raise TypeError("batch must be a PriceBatch")
        actual_symbols = {item.bar.symbol for item in batch.observations}
        if actual_symbols != set(batch.query.symbols):
            raise PriceCacheIntegrityError("cannot cache a symbol-incomplete response")
        if not batch.observations:
            raise PriceCacheIntegrityError("cannot cache an empty response")

        target = self.entry_path(batch.transport, batch.provider, batch.query)
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._accept_identical_or_raise(target, batch)
            _fsync_directory(parent)
            return

        temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=parent))
        published = False
        try:
            parquet_path = temporary / "data.parquet"
            pd.DataFrame(_batch_rows(batch), columns=_COLUMNS).to_parquet(
                parquet_path, index=False
            )
            _fsync_file(parquet_path)
            metadata = _metadata(batch, _sha256_file(parquet_path))
            metadata_path = temporary / "metadata.json"
            metadata_path.write_text(canonical_json(metadata) + "\n", encoding="utf-8")
            _fsync_file(metadata_path)
            _fsync_directory(temporary)
            self._fail("after_temp_entry")
            self._fail("before_publish")
            try:
                os.rename(temporary, target)
                published = True
            except OSError:
                if not target.exists():
                    raise
                self._accept_identical_or_raise(target, batch)
            self._fail("after_publish")
            self._fail("before_parent_fsync")
            _fsync_directory(parent)
            self._fail("after_parent_fsync")
        finally:
            if not published and temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)

    def _accept_identical_or_raise(self, target: Path, batch: PriceBatch) -> None:
        existing = self._load_entry(
            target, batch.transport, batch.provider, batch.query
        )
        if existing != batch:
            raise PriceCacheIntegrityError(
                "conflicting immutable cache entry for identical source query"
            )


class CachedDailyPriceSource:
    """Cache-through wrapper that retains explicit hit/miss provenance."""

    def __init__(self, source: DailyPriceSource, cache: PriceCache) -> None:
        self.source = source
        self.cache = cache
        self.transport = _safe_component(source.transport, "transport")
        self.provider = _safe_component(source.provider, "provider")

    def fetch(self, query: PriceQuery) -> PriceFetchResult:
        cached = self.cache.load(self.transport, self.provider, query)
        if cached is not None:
            return PriceFetchResult(batch=cached, cache_status="hit")
        batch = self.source.fetch(query)
        validate_batch_identity(
            batch,
            transport=self.transport,
            provider=self.provider,
            query=query,
        )
        self.cache.store(batch)
        return PriceFetchResult(batch=batch, cache_status="miss")
