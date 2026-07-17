"""Market-data sources, immutable cache, and provider-neutral contracts."""

from retailtrader.data.cache import (
    CachedDailyPriceSource,
    DailyPriceLoader,
    PriceCache,
    PriceCacheIntegrityError,
    PriceFetchResult,
)
from retailtrader.data.openbb import (
    OpenBBDataError,
    OpenBBUnavailableError,
    OpenBBYFinancePriceSource,
)
from retailtrader.data.replay import (
    REFERENCE_METHOD_VERSION,
    build_price_frames,
    build_reference_indices,
    history_as_of,
    market_close_utc,
    market_open_utc,
    weekly_session_pairs,
)
from retailtrader.data.protocol import (
    AvailableMarketBar,
    DailyPriceSource,
    PriceBatch,
    PriceQuery,
    canonical_json,
    canonical_normalized_rows,
    normalized_rows_hash,
    query_key,
    validate_batch_identity,
)

__all__ = [
    "AvailableMarketBar",
    "CachedDailyPriceSource",
    "DailyPriceLoader",
    "DailyPriceSource",
    "OpenBBDataError",
    "OpenBBUnavailableError",
    "OpenBBYFinancePriceSource",
    "PriceBatch",
    "PriceCache",
    "PriceCacheIntegrityError",
    "PriceFetchResult",
    "PriceQuery",
    "REFERENCE_METHOD_VERSION",
    "build_price_frames",
    "build_reference_indices",
    "canonical_json",
    "history_as_of",
    "market_close_utc",
    "market_open_utc",
    "canonical_normalized_rows",
    "normalized_rows_hash",
    "query_key",
    "validate_batch_identity",
    "weekly_session_pairs",
]
