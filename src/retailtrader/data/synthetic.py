"""Deterministic synthetic market data for the demo build.

3-hour demo mode replaces the live OpenBB adapter with this provider.
Every series is derived from a per-symbol seed, so any call with the same
arguments returns identical data — no network, no cache, no drift.

Fundamental metric names match what factors.py consumes:
nopat, invested_capital, free_cash_flow, market_cap, total_debt, ebitda,
revenue, eps, pe.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from functools import lru_cache

import numpy as np

from retailtrader.domain import FundamentalObservation, MarketBar, MarketSnapshot

EPOCH = date(2023, 1, 2)
HORIZON = date(2026, 7, 15)
AVAILABILITY_LAG_DAYS = 45  # docs/decisions/0002-point-in-time-approximation.md

FUNDAMENTAL_METRICS = (
    "nopat",
    "invested_capital",
    "free_cash_flow",
    "market_cap",
    "total_debt",
    "ebitda",
    "revenue",
    "eps",
    "pe",
)


def _seed(symbol: str) -> int:
    return int.from_bytes(hashlib.sha256(symbol.encode()).digest()[:8], "big")


def trading_sessions(start: date, end: date) -> list[date]:
    """Weekday sessions in [start, end]. ponytail: no holiday calendar."""
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


@lru_cache(maxsize=64)
def _price_series(symbol: str) -> tuple[MarketBar, ...]:
    rng = np.random.default_rng(_seed(symbol))
    sessions = trading_sessions(EPOCH, HORIZON)
    n = len(sessions)
    drift = rng.uniform(0.0001, 0.0009)
    vol = rng.uniform(0.010, 0.028)
    start_price = rng.uniform(40, 600)
    returns = rng.normal(drift, vol, n - 1)
    closes = start_price * np.cumprod(np.concatenate([[1.0], 1 + returns]))
    highs = closes * (1 + rng.uniform(0.001, 0.02, n))
    lows = closes * (1 - rng.uniform(0.001, 0.02, n))
    volumes = rng.integers(1_000_000, 40_000_000, n)

    bars = []
    prev_close = start_price
    for i, session in enumerate(sessions):
        close = float(closes[i])
        open_ = prev_close
        high = max(float(highs[i]), open_, close)
        low = min(float(lows[i]), open_, close)
        bars.append(
            MarketBar(
                symbol=symbol,
                session=session,
                open=Decimal(f"{open_:.2f}"),
                high=Decimal(f"{high:.2f}"),
                low=Decimal(f"{low:.2f}"),
                close=Decimal(f"{close:.2f}"),
                volume=int(volumes[i]),
            )
        )
        prev_close = close
    return tuple(bars)


def price_history(symbol: str, as_of: datetime) -> tuple[MarketBar, ...]:
    """All bars strictly before as_of's session (execution bar excluded)."""
    cutoff = as_of.date()
    return tuple(b for b in _price_series(symbol) if b.session < cutoff)


def _quarter_ends(through: date) -> list[date]:
    ends = []
    for year in range(EPOCH.year, through.year + 1):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            q = date(year, month, day)
            if EPOCH <= q <= through:
                ends.append(q)
    return ends


@lru_cache(maxsize=64)
def _fundamental_series(symbol: str) -> tuple[FundamentalObservation, ...]:
    rng = np.random.default_rng(_seed(symbol) ^ 0xF0F0)
    base = {
        "revenue": rng.uniform(5e9, 120e9),
        "nopat": 0.0,
        "invested_capital": rng.uniform(2e10, 3e11),
        "free_cash_flow": 0.0,
        "market_cap": rng.uniform(5e10, 3e12),
        "total_debt": rng.uniform(5e9, 1.2e11),
        "ebitda": 0.0,
        "eps": rng.uniform(2, 18),
        "pe": rng.uniform(12, 45),
    }
    margin = rng.uniform(0.08, 0.30)
    growth = rng.uniform(-0.01, 0.05)
    # Earnings compound on their own path (buybacks, operating leverage), so eps
    # growth must not be a copy of revenue growth — the factor panel shows both.
    eps_growth = growth + rng.uniform(-0.015, 0.025)

    observations = []
    for i, period_end in enumerate(_quarter_ends(HORIZON)):
        factor = (1 + growth) ** i
        revenue = base["revenue"] * factor
        values = {
            "revenue": revenue,
            "nopat": revenue * margin * 0.7,
            "invested_capital": base["invested_capital"] * (1 + growth / 2) ** i,
            "free_cash_flow": revenue * margin * rng.uniform(0.5, 0.9),
            "market_cap": base["market_cap"] * factor,
            "total_debt": base["total_debt"],
            "ebitda": revenue * margin * 1.3,
            "eps": base["eps"] * (1 + eps_growth) ** i,
            "pe": base["pe"] * rng.uniform(0.9, 1.1),
        }
        available = datetime.combine(
            period_end + timedelta(days=AVAILABILITY_LAG_DAYS), time(12), tzinfo=UTC
        )
        for metric, value in values.items():
            observations.append(
                FundamentalObservation(
                    symbol=symbol,
                    metric=metric,
                    value=round(float(value), 4),
                    period_end=period_end,
                    available_at=available,
                    as_of=available,
                    availability_source="approximated",
                )
            )
    return tuple(observations)


def fundamentals(symbol: str, as_of: datetime) -> tuple[FundamentalObservation, ...]:
    """Observations available at as_of, re-stamped to the query as_of."""
    return tuple(
        obs.model_copy(update={"as_of": as_of})
        for obs in _fundamental_series(symbol)
        if obs.available_at <= as_of
    )


def snapshot_for(symbols: tuple[str, ...], session: date) -> MarketSnapshot:
    """Post-close snapshot for one session: that day's bars + eligible fundamentals."""
    as_of = datetime.combine(session, time(20), tzinfo=UTC)
    bars = []
    for symbol in symbols:
        day = [b for b in _price_series(symbol) if b.session == session]
        if day:
            bars.append(day[0])
    funds: list[FundamentalObservation] = []
    for symbol in symbols:
        funds.extend(fundamentals(symbol, as_of))
    return MarketSnapshot(as_of=as_of, bars=tuple(bars), fundamentals=tuple(funds))


def decision_snapshot_for(
    symbols: tuple[str, ...], execution_session: date
) -> MarketSnapshot:
    """Snapshot capped at the completed session before an execution open."""
    prior_sessions = trading_sessions(EPOCH, execution_session - timedelta(days=1))
    if not prior_sessions:
        raise ValueError(f"no completed session before {execution_session}")
    decision_session = prior_sessions[-1]
    as_of = datetime.combine(decision_session, time(20), tzinfo=UTC)
    bars = tuple(
        bar
        for symbol in symbols
        for bar in _price_series(symbol)
        if bar.session == decision_session
    )
    funds = tuple(
        observation
        for symbol in symbols
        for observation in fundamentals(symbol, as_of)
    )
    return MarketSnapshot(as_of=as_of, bars=bars, fundamentals=funds)
