"""Market-data sources, immutable cache, and provider-neutral contracts."""

from retailtrader.data.cache import (
    CachedDailyPriceSource,
    DailyPriceLoader,
    PriceCache,
    PriceCacheIntegrityError,
    PriceFetchResult,
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
    "PriceBatch",
    "PriceCache",
    "PriceCacheIntegrityError",
    "PriceFetchResult",
    "PriceQuery",
    "canonical_json",
    "canonical_normalized_rows",
    "normalized_rows_hash",
    "query_key",
    "validate_batch_identity",
]
