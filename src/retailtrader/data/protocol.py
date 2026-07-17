"""Provider-neutral contracts for point-in-time daily market prices."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from retailtrader.domain import MarketBar

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_NEW_YORK = ZoneInfo("America/New_York")


def _as_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _require_nonempty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be nonempty")


def _require_sha256(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")


@dataclass(frozen=True)
class PriceQuery:
    """An inclusive query for adjusted daily bars."""

    symbols: tuple[str, ...]
    start: date
    end: date
    interval: Literal["1d"] = "1d"
    adjustment: Literal["splits_and_dividends"] = "splits_and_dividends"

    def __post_init__(self) -> None:
        if isinstance(self.symbols, str) or not isinstance(self.symbols, Sequence):
            raise TypeError("query symbols must be a non-string sequence of strings")
        if any(not isinstance(symbol, str) for symbol in self.symbols):
            raise TypeError("query symbols must contain only strings")
        if not isinstance(self.start, date) or isinstance(self.start, datetime):
            raise TypeError("query start must be a date, not a datetime or string")
        if not isinstance(self.end, date) or isinstance(self.end, datetime):
            raise TypeError("query end must be a date, not a datetime or string")
        if not isinstance(self.interval, str):
            raise TypeError("query interval must be a string")
        if self.interval != "1d":
            raise ValueError(f"unsupported query interval {self.interval!r}; only '1d' is supported")
        if not isinstance(self.adjustment, str):
            raise TypeError("query adjustment must be a string")
        if self.adjustment != "splits_and_dividends":
            raise ValueError(
                "unsupported query adjustment "
                f"{self.adjustment!r}; only 'splits_and_dividends' is supported"
            )

        if not self.symbols:
            raise ValueError("query requires at least one symbol")
        normalized_symbols: list[str] = []
        for symbol in self.symbols:
            if not symbol.strip():
                raise ValueError("symbol must be nonempty")
            normalized_symbols.append(symbol.strip().upper())
        if self.start > self.end:
            raise ValueError("query start must not be after end")
        object.__setattr__(self, "symbols", tuple(sorted(set(normalized_symbols))))


@dataclass(frozen=True)
class AvailableMarketBar:
    """A daily bar together with the instants its prices became observable."""

    bar: MarketBar
    open_available_at: datetime
    close_available_at: datetime
    source_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.bar, MarketBar):
            raise TypeError("bar must be a MarketBar")
        _require_nonempty(self.source_ref, "source_ref")
        open_available_at = _as_utc(self.open_available_at, "open_available_at")
        close_available_at = _as_utc(self.close_available_at, "close_available_at")
        object.__setattr__(self, "open_available_at", open_available_at)
        object.__setattr__(self, "close_available_at", close_available_at)

        if open_available_at >= close_available_at:
            raise ValueError("open availability must be before close availability")

        session_open = datetime.combine(self.bar.session, time(9, 30), tzinfo=_NEW_YORK).astimezone(
            UTC
        )
        session_close = datetime.combine(self.bar.session, time(16), tzinfo=_NEW_YORK).astimezone(
            UTC
        )
        if open_available_at < session_open:
            raise ValueError("open availability cannot precede canonical session open")
        if close_available_at < session_close:
            raise ValueError("close availability cannot precede canonical session close")


def canonical_normalized_rows(
    observations: Sequence[AvailableMarketBar],
) -> tuple[dict[str, object], ...]:
    """Render observations as stable, sorted provider-independent row mappings."""

    sorted_observations = sorted(
        observations,
        key=lambda observation: (observation.bar.session, observation.bar.symbol),
    )
    return tuple(
        {
            "symbol": observation.bar.symbol,
            "session": observation.bar.session,
            "open": observation.bar.open,
            "high": observation.bar.high,
            "low": observation.bar.low,
            "close": observation.bar.close,
            "volume": observation.bar.volume,
            "open_available_at": observation.open_available_at,
            "close_available_at": observation.close_available_at,
            "source_ref": observation.source_ref,
        }
        for observation in sorted_observations
    )


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, datetime):
        return _as_utc(value, "datetime").isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical JSON mapping keys must be strings")
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _canonical_value(getattr(value, field.name)) for field in fields(value)}
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: object) -> str:
    """Serialize supported contract values as compact, deterministic JSON."""

    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def normalized_rows_hash(observations: Sequence[AvailableMarketBar]) -> str:
    """Return the SHA-256 digest of canonical normalized observation rows."""

    payload = canonical_json(canonical_normalized_rows(observations)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _canonical_query(query: PriceQuery) -> dict[str, object]:
    return {
        "symbols": query.symbols,
        "start": query.start,
        "end": query.end,
        "interval": query.interval,
        "adjustment": query.adjustment,
    }


def query_key(transport: str, provider: str, query: PriceQuery) -> str:
    """Hash every source-identity and query dimension into a stable cache key."""

    _require_nonempty(transport, "transport")
    _require_nonempty(provider, "provider")
    if not isinstance(query, PriceQuery):
        raise TypeError("query must be a PriceQuery")
    payload = canonical_json(
        {
            "transport": transport,
            "provider": provider,
            "query": _canonical_query(query),
        }
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class PriceBatch:
    """Validated result of one daily-price source query."""

    transport: str
    provider: str
    query: PriceQuery
    observations: tuple[AvailableMarketBar, ...]
    retrieved_at: datetime
    raw_hash: str
    normalized_hash: str
    provider_versions: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _require_nonempty(self.transport, "transport")
        _require_nonempty(self.provider, "provider")
        if not isinstance(self.query, PriceQuery):
            raise TypeError("query must be a PriceQuery")

        retrieved_at = _as_utc(self.retrieved_at, "retrieved_at")
        object.__setattr__(self, "retrieved_at", retrieved_at)
        immutable_observations = tuple(self.observations)
        if not all(
            isinstance(observation, AvailableMarketBar) for observation in immutable_observations
        ):
            raise TypeError("observations must contain AvailableMarketBar values")
        observations = tuple(
            sorted(
                immutable_observations,
                key=lambda observation: (observation.bar.session, observation.bar.symbol),
            )
        )
        object.__setattr__(self, "observations", observations)

        seen: set[tuple[str, date]] = set()
        requested_symbols = set(self.query.symbols)
        for observation in observations:
            identity = (observation.bar.symbol, observation.bar.session)
            if identity in seen:
                raise ValueError(f"duplicate observation for {identity[0]} on {identity[1]}")
            seen.add(identity)
            if observation.bar.symbol not in requested_symbols:
                raise ValueError(f"observation symbol {observation.bar.symbol} was not requested")
            if not self.query.start <= observation.bar.session <= self.query.end:
                raise ValueError(
                    f"observation session {observation.bar.session} is outside query range"
                )
            if observation.open_available_at >= observation.close_available_at:
                raise ValueError("open availability must be before close availability")
            if observation.close_available_at > retrieved_at:
                raise ValueError("close availability cannot be after retrieval")

        self._validate_provider_versions()
        _require_sha256(self.raw_hash, "raw_hash")
        _require_sha256(self.normalized_hash, "normalized_hash")
        expected_normalized_hash = normalized_rows_hash(observations)
        if self.normalized_hash != expected_normalized_hash:
            raise ValueError("normalized_hash does not match canonical normalized rows")

    def _validate_provider_versions(self) -> None:
        versions = self.provider_versions
        if not isinstance(versions, tuple) or any(
            not isinstance(item, tuple) or len(item) != 2 for item in versions
        ):
            raise ValueError("provider_versions must be an immutable tuple of pairs")
        for package, version in versions:
            _require_nonempty(package, "provider version package")
            _require_nonempty(version, "provider version")
        packages = [package for package, _ in versions]
        if tuple(sorted(versions)) != versions or len(packages) != len(set(packages)):
            raise ValueError("provider_versions must be sorted and unique")

    @classmethod
    def create(
        cls,
        *,
        transport: str,
        provider: str,
        query: PriceQuery,
        observations: tuple[AvailableMarketBar, ...],
        retrieved_at: datetime,
        raw_hash: str,
        provider_versions: tuple[tuple[str, str], ...],
    ) -> PriceBatch:
        """Build a batch while deriving its normalized hash exactly once."""

        immutable_observations = tuple(observations)
        return cls(
            transport=transport,
            provider=provider,
            query=query,
            observations=immutable_observations,
            retrieved_at=retrieved_at,
            raw_hash=raw_hash,
            normalized_hash=normalized_rows_hash(immutable_observations),
            provider_versions=provider_versions,
        )


def validate_batch_identity(
    batch: PriceBatch,
    *,
    transport: str,
    provider: str,
    query: PriceQuery | None = None,
) -> None:
    """Reject a batch that does not match its configured source (and optional query)."""

    expected: tuple[object, ...] = (transport, provider)
    actual: tuple[object, ...] = (batch.transport, batch.provider)
    if query is not None:
        expected += (query,)
        actual += (batch.query,)
    if actual != expected:
        raise ValueError(f"batch identity mismatch: expected {expected!r}, got {actual!r}")


@runtime_checkable
class DailyPriceSource(Protocol):
    """Provider interface for availability-bearing adjusted daily prices."""

    transport: str
    provider: str

    def fetch(self, query: PriceQuery) -> PriceBatch: ...
