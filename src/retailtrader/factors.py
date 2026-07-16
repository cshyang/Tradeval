"""Audited factor catalog: pure, deterministic factor functions.

Every factor returns a :class:`FactorObservation` carrying exactly one of
``value`` or ``unavailable_reason`` — missing data never silently becomes
zero. Price factors use only bars whose session is on or before ``as_of``
(the execution bar fills at the *next* session open and is never visible).
Fundamental factors use only observations with ``available_at <= as_of``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from statistics import fmean, stdev

from retailtrader.domain import FactorObservation, FundamentalObservation, MarketBar

FORMULA_VERSION = "1.0"

MOMENTUM_6M_SESSIONS = 126
MOMENTUM_12M_SESSIONS = 252
SMA_SESSIONS = 200
VOLATILITY_SESSIONS = 60
TRADING_DAYS_PER_YEAR = 252
FCF_CONSISTENCY_PERIODS = 4
GROWTH_PERIODS = 4  # latest + 3 prior annual observations


def _available(
    symbol: str, metric: str, value: float, as_of: datetime, refs: tuple[str, ...]
) -> FactorObservation:
    return FactorObservation(
        symbol=symbol,
        metric=metric,
        value=value,
        formula_version=FORMULA_VERSION,
        source_refs=refs,
        as_of=as_of,
    )


def _unavailable(
    symbol: str, metric: str, reason: str, as_of: datetime
) -> FactorObservation:
    return FactorObservation(
        symbol=symbol,
        metric=metric,
        unavailable_reason=reason,
        formula_version=FORMULA_VERSION,
        as_of=as_of,
    )


def _usable_closes(
    symbol: str, bars: Sequence[MarketBar], as_of: datetime
) -> tuple[list[float], tuple[str, ...]]:
    """Closes for ``symbol`` strictly up to ``as_of`` (execution bar excluded)."""
    by_session = {
        b.session: b for b in bars if b.symbol == symbol and b.session <= as_of.date()
    }
    sessions = sorted(by_session)
    closes = [float(by_session[s].close) for s in sessions]
    refs: tuple[str, ...] = ()
    if sessions:
        refs = (f"bars:{symbol}:{sessions[0].isoformat()}..{sessions[-1].isoformat()}",)
    return closes, refs


def _momentum(
    symbol: str,
    metric: str,
    bars: Sequence[MarketBar],
    as_of: datetime,
    lookback: int,
) -> FactorObservation:
    closes, refs = _usable_closes(symbol, bars, as_of)
    if len(closes) < lookback + 1:
        return _unavailable(
            symbol, metric, f"insufficient history: need {lookback + 1} sessions", as_of
        )
    base = closes[-1 - lookback]
    if base <= 0:
        return _unavailable(symbol, metric, "non-positive base price", as_of)
    return _available(symbol, metric, closes[-1] / base - 1.0, as_of, refs)


def momentum_6m(
    symbol: str, bars: Sequence[MarketBar], as_of: datetime
) -> FactorObservation:
    return _momentum(symbol, "momentum_6m", bars, as_of, MOMENTUM_6M_SESSIONS)


def momentum_12m(
    symbol: str, bars: Sequence[MarketBar], as_of: datetime
) -> FactorObservation:
    return _momentum(symbol, "momentum_12m", bars, as_of, MOMENTUM_12M_SESSIONS)


def above_sma_200(
    symbol: str, bars: Sequence[MarketBar], as_of: datetime
) -> FactorObservation:
    closes, refs = _usable_closes(symbol, bars, as_of)
    if len(closes) < SMA_SESSIONS:
        return _unavailable(
            symbol,
            "above_sma_200",
            f"insufficient history: need {SMA_SESSIONS} sessions",
            as_of,
        )
    sma = fmean(closes[-SMA_SESSIONS:])
    return _available(symbol, "above_sma_200", closes[-1] / sma - 1.0, as_of, refs)


def volatility_60d(
    symbol: str, bars: Sequence[MarketBar], as_of: datetime
) -> FactorObservation:
    closes, refs = _usable_closes(symbol, bars, as_of)
    if len(closes) < VOLATILITY_SESSIONS + 1:
        return _unavailable(
            symbol,
            "volatility_60d",
            f"insufficient history: need {VOLATILITY_SESSIONS + 1} sessions",
            as_of,
        )
    window = closes[-(VOLATILITY_SESSIONS + 1) :]
    returns = [window[i] / window[i - 1] - 1.0 for i in range(1, len(window))]
    annualized = stdev(returns) * TRADING_DAYS_PER_YEAR**0.5
    return _available(symbol, "volatility_60d", annualized, as_of, refs)


def _eligible(
    fundamentals: Sequence[FundamentalObservation],
    symbol: str,
    metric: str,
    as_of: datetime,
) -> list[FundamentalObservation]:
    """Point-in-time filter: only observations available by ``as_of``."""
    rows = [
        f
        for f in fundamentals
        if f.symbol == symbol and f.metric == metric and f.available_at <= as_of
    ]
    rows.sort(key=lambda f: (f.period_end, f.available_at))
    return rows


def _latest_value(
    fundamentals: Sequence[FundamentalObservation],
    symbol: str,
    metric: str,
    as_of: datetime,
) -> tuple[float, str] | None:
    rows = _eligible(fundamentals, symbol, metric, as_of)
    if not rows:
        return None
    latest = rows[-1]
    return latest.value, f"fundamental:{symbol}:{metric}:{latest.period_end.isoformat()}"


def _ratio_factor(
    symbol: str,
    metric: str,
    numerator_metric: str,
    denominator_metric: str,
    fundamentals: Sequence[FundamentalObservation],
    as_of: datetime,
    denominator_rule: str,
) -> FactorObservation:
    numerator = _latest_value(fundamentals, symbol, numerator_metric, as_of)
    denominator = _latest_value(fundamentals, symbol, denominator_metric, as_of)
    if numerator is None:
        return _unavailable(symbol, metric, f"no {numerator_metric} observation", as_of)
    if denominator is None:
        return _unavailable(symbol, metric, f"no {denominator_metric} observation", as_of)
    if denominator[0] <= 0:
        return _unavailable(symbol, metric, denominator_rule, as_of)
    return _available(
        symbol, metric, numerator[0] / denominator[0], as_of, (numerator[1], denominator[1])
    )


def roic(
    symbol: str, fundamentals: Sequence[FundamentalObservation], as_of: datetime
) -> FactorObservation:
    return _ratio_factor(
        symbol,
        "roic",
        "nopat",
        "invested_capital",
        fundamentals,
        as_of,
        "non-positive invested capital",
    )


def fcf_yield(
    symbol: str, fundamentals: Sequence[FundamentalObservation], as_of: datetime
) -> FactorObservation:
    return _ratio_factor(
        symbol,
        "fcf_yield",
        "free_cash_flow",
        "market_cap",
        fundamentals,
        as_of,
        "non-positive market cap",
    )


def debt_to_ebitda(
    symbol: str, fundamentals: Sequence[FundamentalObservation], as_of: datetime
) -> FactorObservation:
    return _ratio_factor(
        symbol,
        "debt_to_ebitda",
        "total_debt",
        "ebitda",
        fundamentals,
        as_of,
        "non-positive EBITDA",
    )


def free_cash_flow_consistency(
    symbol: str, fundamentals: Sequence[FundamentalObservation], as_of: datetime
) -> FactorObservation:
    rows = _eligible(fundamentals, symbol, "free_cash_flow", as_of)
    if len(rows) < FCF_CONSISTENCY_PERIODS:
        return _unavailable(
            symbol,
            "free_cash_flow_consistency",
            f"insufficient fundamental history: need {FCF_CONSISTENCY_PERIODS} periods",
            as_of,
        )
    window = rows[-FCF_CONSISTENCY_PERIODS:]
    positive = sum(1 for f in window if f.value > 0)
    refs = tuple(
        f"fundamental:{symbol}:free_cash_flow:{f.period_end.isoformat()}" for f in window
    )
    return _available(
        symbol,
        "free_cash_flow_consistency",
        positive / FCF_CONSISTENCY_PERIODS,
        as_of,
        refs,
    )


def _growth_3y(
    symbol: str,
    metric: str,
    input_metric: str,
    fundamentals: Sequence[FundamentalObservation],
    as_of: datetime,
) -> FactorObservation:
    rows = _eligible(fundamentals, symbol, input_metric, as_of)
    if len(rows) < GROWTH_PERIODS:
        return _unavailable(
            symbol,
            metric,
            f"insufficient fundamental history: need {GROWTH_PERIODS} periods",
            as_of,
        )
    window = rows[-GROWTH_PERIODS:]
    base, latest = window[0].value, window[-1].value
    if base <= 0:
        return _unavailable(symbol, metric, f"non-positive base {input_metric}", as_of)
    if latest <= 0:
        return _unavailable(symbol, metric, f"non-positive latest {input_metric}", as_of)
    cagr = (latest / base) ** (1.0 / (GROWTH_PERIODS - 1)) - 1.0
    refs = tuple(
        f"fundamental:{symbol}:{input_metric}:{f.period_end.isoformat()}" for f in window
    )
    return _available(symbol, metric, cagr, as_of, refs)


def revenue_growth_3y(
    symbol: str, fundamentals: Sequence[FundamentalObservation], as_of: datetime
) -> FactorObservation:
    return _growth_3y(symbol, "revenue_growth_3y", "revenue", fundamentals, as_of)


def eps_growth_3y(
    symbol: str, fundamentals: Sequence[FundamentalObservation], as_of: datetime
) -> FactorObservation:
    return _growth_3y(symbol, "eps_growth_3y", "eps", fundamentals, as_of)


def growth_adjusted_pe(
    symbol: str, fundamentals: Sequence[FundamentalObservation], as_of: datetime
) -> FactorObservation:
    """PEG-style ratio: trailing P/E divided by 3-year EPS growth in percent."""
    metric = "growth_adjusted_pe"
    pe = _latest_value(fundamentals, symbol, "pe", as_of)
    if pe is None:
        return _unavailable(symbol, metric, "no pe observation", as_of)
    if pe[0] <= 0:
        return _unavailable(symbol, metric, "non-positive P/E", as_of)
    growth = eps_growth_3y(symbol, fundamentals, as_of)
    if growth.value is None:
        return _unavailable(
            symbol, metric, f"eps growth unavailable: {growth.unavailable_reason}", as_of
        )
    if growth.value <= 0:
        return _unavailable(symbol, metric, "non-positive EPS growth", as_of)
    value = pe[0] / (growth.value * 100.0)
    return _available(symbol, metric, value, as_of, (pe[1], *growth.source_refs))


PriceFactor = Callable[[str, Sequence[MarketBar], datetime], FactorObservation]
FundamentalFactor = Callable[
    [str, Sequence[FundamentalObservation], datetime], FactorObservation
]

PRICE_FACTORS: dict[str, PriceFactor] = {
    "momentum_6m": momentum_6m,
    "momentum_12m": momentum_12m,
    "above_sma_200": above_sma_200,
    "volatility_60d": volatility_60d,
}

FUNDAMENTAL_FACTORS: dict[str, FundamentalFactor] = {
    "roic": roic,
    "fcf_yield": fcf_yield,
    "debt_to_ebitda": debt_to_ebitda,
    "free_cash_flow_consistency": free_cash_flow_consistency,
    "revenue_growth_3y": revenue_growth_3y,
    "eps_growth_3y": eps_growth_3y,
    "growth_adjusted_pe": growth_adjusted_pe,
}

CATALOG: frozenset[str] = frozenset(PRICE_FACTORS) | frozenset(FUNDAMENTAL_FACTORS)


def compute_factor(
    name: str,
    symbol: str,
    as_of: datetime,
    *,
    bars: Sequence[MarketBar] = (),
    fundamentals: Sequence[FundamentalObservation] = (),
) -> FactorObservation:
    """Dispatch a catalog factor by name. Raises ``KeyError`` for unknown names."""
    if name in PRICE_FACTORS:
        return PRICE_FACTORS[name](symbol, bars, as_of)
    if name in FUNDAMENTAL_FACTORS:
        return FUNDAMENTAL_FACTORS[name](symbol, fundamentals, as_of)
    raise KeyError(f"unknown factor: {name}")
