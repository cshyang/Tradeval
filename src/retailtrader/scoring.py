"""Deterministic scoring pipeline: philosophy spec + market snapshot → target.

Fixed processing order (never reordered):

    eligibility → factor availability (coverage) → cross-sectional
    percentile normalization → weighted score → deterministic ranking
    (ascending-symbol tie-break) → top-N → equal weight → constraints
    → TargetPortfolio

``generate_target`` is the single entry point. It also emits one decision
record per invocation with score attribution for selected AND rejected
symbols, matching the ``decisions.jsonl`` artifact shape.

A :class:`MarketSnapshot` carries one bar per symbol (the decision session).
Price factors need lookback history, supplied via the optional ``history``
mapping; without it, price factors report ``unavailable`` rather than
fabricating values.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from retailtrader.allocation import allocate
from retailtrader.domain import (
    EligibilityFilter,
    FactorObservation,
    MarketBar,
    MarketSnapshot,
    PhilosophySpec,
    TargetPortfolio,
)
from retailtrader.factors import PRICE_FACTORS, compute_factor

DecisionRecord = dict[str, Any]


def _bars_for(
    symbol: str,
    snapshot: MarketSnapshot,
    history: Mapping[str, Sequence[MarketBar]] | None,
) -> tuple[MarketBar, ...]:
    by_session = {}
    if history is not None:
        for bar in history.get(symbol, ()):
            by_session[bar.session] = bar
    for bar in snapshot.bars:
        if bar.symbol == symbol:
            by_session[bar.session] = bar
    return tuple(by_session[s] for s in sorted(by_session))


def _passes(filter_: EligibilityFilter, value: float) -> bool:
    if filter_.op == "between":
        low, high = filter_.value
        return low <= value <= high
    threshold = filter_.value
    match filter_.op:
        case "gt":
            return value > threshold
        case "gte":
            return value >= threshold
        case "lt":
            return value < threshold
        case "lte":
            return value <= threshold
        case "eq":
            return value == threshold
    raise ValueError(f"unsupported operator: {filter_.op}")


def _percentile(values: Sequence[float], v: float) -> float:
    """Mid-rank percentile of ``v`` within ``values`` (which includes ``v``)."""
    less = sum(1 for x in values if x < v)
    equal = sum(1 for x in values if x == v)
    return (less + 0.5 * equal) / len(values)


def generate_target(
    spec: PhilosophySpec,
    snapshot: MarketSnapshot,
    run_id: str,
    *,
    history: Mapping[str, Sequence[MarketBar]] | None = None,
) -> tuple[TargetPortfolio, list[DecisionRecord]]:
    """Run the fixed pipeline and return the target plus decision records."""
    as_of = snapshot.as_of
    symbols = sorted({bar.symbol for bar in snapshot.bars})

    metric_names = sorted(
        {f.name for f in spec.factors} | {f.metric for f in spec.filters}
    )
    observations: dict[str, dict[str, FactorObservation]] = {}
    for symbol in symbols:
        bars = _bars_for(symbol, snapshot, history)
        observations[symbol] = {
            name: compute_factor(
                name,
                symbol,
                as_of,
                bars=bars if name in PRICE_FACTORS else (),
                fundamentals=snapshot.fundamentals,
            )
            for name in metric_names
        }

    # Stage 1: eligibility filters. Unavailable filter metrics fail closed.
    rejections: dict[str, str] = {}
    for symbol in symbols:
        for filter_ in spec.filters:
            obs = observations[symbol][filter_.metric]
            if obs.value is None:
                rejections[symbol] = (
                    f"filter metric {filter_.metric} unavailable: {obs.unavailable_reason}"
                )
                break
            if not _passes(filter_, obs.value):
                rejections[symbol] = (
                    f"failed filter: {filter_.metric} {filter_.op} {filter_.value}"
                )
                break

    # Stage 2: factor availability (coverage).
    coverage: dict[str, float] = {}
    for symbol in symbols:
        available = sum(
            1 for f in spec.factors if observations[symbol][f.name].value is not None
        )
        coverage[symbol] = available / len(spec.factors)
        if symbol not in rejections and coverage[symbol] < spec.min_factor_coverage:
            rejections[symbol] = "insufficient factor coverage"

    # Stage 3: cross-sectional percentile normalization over the full
    # cross-section, so rejected symbols still receive honest scores.
    percentiles: dict[str, dict[str, float]] = {s: {} for s in symbols}
    for factor in spec.factors:
        cross_section = {
            s: observations[s][factor.name].value
            for s in symbols
            if observations[s][factor.name].value is not None
        }
        values = list(cross_section.values())
        for symbol, value in cross_section.items():
            pct = _percentile(values, value)
            if factor.direction == "lower_is_better":
                pct = 1.0 - pct
            percentiles[symbol][factor.name] = pct

    # Stage 4: weighted score, renormalized over available factor weights.
    scores: dict[str, float | None] = {}
    contributions: dict[str, dict[str, float]] = {s: {} for s in symbols}
    for symbol in symbols:
        available = [f for f in spec.factors if f.name in percentiles[symbol]]
        total_weight = sum(f.weight for f in available)
        if not available:
            scores[symbol] = None
            continue
        for factor in available:
            contributions[symbol][factor.name] = (
                factor.weight / total_weight
            ) * percentiles[symbol][factor.name]
        scores[symbol] = sum(contributions[symbol].values())

    # Stage 5: deterministic ranking with ascending-symbol tie-break.
    candidates = [s for s in symbols if s not in rejections]
    ranked = sorted(candidates, key=lambda s: (-(scores[s] or 0.0), s))

    # Stage 6: top-N selection; the rest fall below the cutoff.
    selected = ranked[: spec.top_n]
    for symbol in ranked[spec.top_n :]:
        rejections[symbol] = "score below cutoff"

    # Stages 7-8: equal weight + risk constraints → target portfolio.
    portfolio = allocate(spec, selected, run_id, as_of)
    weights = {p.symbol: p.weight for p in portfolio.positions}

    record: DecisionRecord = {
        "as_of": as_of.isoformat(),
        "selected": [
            {
                "symbol": symbol,
                "weight": weights[symbol],
                "score": scores[symbol],
                "factors": [
                    {
                        "name": factor.name,
                        "value": observations[symbol][factor.name].value,
                        "contribution": contributions[symbol].get(factor.name, 0.0),
                    }
                    for factor in spec.factors
                ],
            }
            for symbol in sorted(selected)
        ],
        "rejected": [
            {"symbol": symbol, "reason": rejections[symbol], "score": scores[symbol]}
            for symbol in sorted(rejections)
        ],
    }
    return portfolio, [record]
