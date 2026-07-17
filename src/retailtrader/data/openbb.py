"""OpenBB transport adapter for adjusted Yahoo Finance daily equity bars."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import math
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from retailtrader.data.protocol import (
    AvailableMarketBar,
    PriceBatch,
    PriceQuery,
    canonical_json,
)
from retailtrader.domain import MarketBar

_NEW_YORK = ZoneInfo("America/New_York")
HistoricalCallable = Callable[..., Any]
Clock = Callable[[], datetime]
VersionLookup = Callable[[str], str]
Importer = Callable[[str], Any]


class OpenBBUnavailableError(RuntimeError):
    """The optional OpenBB runtime is not installed."""


class OpenBBDataError(RuntimeError):
    """OpenBB/Yahoo returned incomplete or invalid daily market data."""


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump())
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    raise OpenBBDataError(f"unsupported OpenBB result row: {type(value).__name__}")


def _session(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise OpenBBDataError(f"invalid result date: {value!r}") from exc
    raise OpenBBDataError(f"invalid result date type: {type(value).__name__}")


def _finite_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise OpenBBDataError(f"missing or invalid {field}")
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise OpenBBDataError(f"invalid {field}: {value!r}") from exc
    if not numeric.is_finite() or numeric <= 0:
        raise OpenBBDataError(f"{field} must be finite and positive")
    return numeric


def _volume(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise OpenBBDataError("missing or invalid volume")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise OpenBBDataError(f"invalid volume: {value!r}") from exc
    if not math.isfinite(numeric) or numeric < 0 or not numeric.is_integer():
        raise OpenBBDataError("volume must be a finite non-negative integer")
    return int(numeric)


def _availability(session: date, hour: int, minute: int = 0) -> datetime:
    return datetime(
        session.year,
        session.month,
        session.day,
        hour,
        minute,
        tzinfo=_NEW_YORK,
    ).astimezone(UTC)


class OpenBBYFinancePriceSource:
    """Fetch completed total-return-adjusted daily bars through OpenBB."""

    transport = "openbb"
    provider = "yfinance"

    def __init__(
        self,
        historical: HistoricalCallable | None = None,
        *,
        clock: Clock = _default_clock,
        version_lookup: VersionLookup = importlib.metadata.version,
        importer: Importer = importlib.import_module,
    ) -> None:
        self._injected_historical = historical
        self._clock = clock
        self._version_lookup = version_lookup
        self._importer = importer

    def _historical(self) -> HistoricalCallable:
        if self._injected_historical is not None:
            return self._injected_historical
        try:
            module = self._importer("openbb")
            return module.obb.equity.price.historical
        except (ImportError, ModuleNotFoundError, AttributeError) as exc:
            raise OpenBBUnavailableError(
                "OpenBB/Yahoo support is optional; run "
                "`uv sync --extra data-openbb` before using live market data"
            ) from exc

    def _versions(self) -> tuple[tuple[str, str], ...]:
        try:
            versions = (
                ("openbb", self._version_lookup("openbb")),
                ("openbb-yfinance", self._version_lookup("openbb-yfinance")),
            )
        except importlib.metadata.PackageNotFoundError as exc:
            if self._injected_historical is None:
                raise OpenBBUnavailableError(
                    "OpenBB/Yahoo support is optional; run "
                    "`uv sync --extra data-openbb` before using live market data"
                ) from exc
            raise OpenBBDataError(f"missing provider package version: {exc}") from exc
        return tuple(sorted(versions))

    def fetch(self, query: PriceQuery) -> PriceBatch:
        if not isinstance(query, PriceQuery):
            raise TypeError("query must be a PriceQuery")
        historical = self._historical()
        provider_versions = self._versions()
        retrieved_at = self._clock()
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise ValueError("OpenBB retrieval clock must return an aware datetime")
        retrieved_at = retrieved_at.astimezone(UTC)

        observations: list[AvailableMarketBar] = []
        raw_records: list[dict[str, object]] = []
        seen: set[tuple[str, date]] = set()
        symbols_with_rows: set[str] = set()
        for symbol in query.symbols:
            try:
                response = historical(
                    symbol=symbol,
                    provider=self.provider,
                    interval=query.interval,
                    adjustment=query.adjustment,
                    extended_hours=False,
                    start_date=query.start,
                    end_date=query.end + timedelta(days=1),
                )
            except Exception as exc:
                raise OpenBBDataError(f"OpenBB/Yahoo request failed for {symbol}: {exc}") from exc
            response_provider = getattr(response, "provider", None)
            if response_provider != self.provider:
                raise OpenBBDataError(
                    f"provider mismatch for {symbol}: expected {self.provider}, "
                    f"got {response_provider!r}"
                )
            rows = getattr(response, "results", None)
            if rows is None:
                raise OpenBBDataError(f"OpenBB response for {symbol} has no results")
            for item in rows:
                row = _as_mapping(item)
                session = _session(row.get("date"))
                if session < query.start or session > query.end:
                    continue
                if row.get("intra_period") is True:
                    raise OpenBBDataError(f"unfinished daily bar for {symbol} on {session}")
                identity = (symbol, session)
                if identity in seen:
                    raise OpenBBDataError(f"duplicate daily bar for {symbol} on {session}")
                seen.add(identity)

                open_price = _finite_decimal(row.get("open"), "open")
                high = _finite_decimal(row.get("high"), "high")
                low = _finite_decimal(row.get("low"), "low")
                close = _finite_decimal(row.get("close"), "close")
                if high < max(open_price, close) or low > min(open_price, close) or high < low:
                    raise OpenBBDataError(f"invalid OHLC relationship for {symbol} on {session}")
                volume = _volume(row.get("volume"))
                open_available_at = _availability(session, 9, 30)
                close_available_at = _availability(session, 16)
                if close_available_at > retrieved_at:
                    raise OpenBBDataError(
                        f"daily bar for {symbol} on {session} was not complete at retrieval"
                    )
                source_ref = (
                    f"openbb:equity.price.historical:yfinance:{symbol}:{session.isoformat()}"
                )
                bar = MarketBar(
                    symbol=symbol,
                    session=session,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
                observations.append(
                    AvailableMarketBar(
                        bar=bar,
                        open_available_at=open_available_at,
                        close_available_at=close_available_at,
                        source_ref=source_ref,
                    )
                )
                symbols_with_rows.add(symbol)
                raw_records.append(
                    {
                        "symbol": symbol,
                        "date": session.isoformat(),
                        "open": str(open_price),
                        "high": str(high),
                        "low": str(low),
                        "close": str(close),
                        "volume": volume,
                        "intra_period": bool(row.get("intra_period", False)),
                        "dividend": None
                        if row.get("dividend") is None
                        else str(row.get("dividend")),
                        "split_ratio": None
                        if row.get("split_ratio") is None
                        else str(row.get("split_ratio")),
                    }
                )

        missing = sorted(set(query.symbols) - symbols_with_rows)
        if missing:
            raise OpenBBDataError(f"no completed rows returned for: {', '.join(missing)}")
        raw_records.sort(key=lambda row: (str(row["date"]), str(row["symbol"])))
        raw_hash = hashlib.sha256(canonical_json(raw_records).encode("utf-8")).hexdigest()
        return PriceBatch.create(
            transport=self.transport,
            provider=self.provider,
            query=query,
            observations=tuple(observations),
            retrieved_at=retrieved_at,
            raw_hash=raw_hash,
            provider_versions=provider_versions,
        )
