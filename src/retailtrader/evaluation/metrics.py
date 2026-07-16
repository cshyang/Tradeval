"""Deterministic evaluation metrics computed from run artifact series.

Inputs are the artifact shapes the runner persists: equity.csv rows,
fills.jsonl rows, portfolio.jsonl rows, decisions.jsonl records. Formulas:

- total_return: end / start - 1 (needs >= 2 points, else InsufficientHistoryError)
- cagr: (end / start) ** (365.25 / days) - 1 over the calendar span
- volatility: sample stdev of periodic returns * sqrt(periods per year),
  periods per year inferred from mean spacing (needs >= 3 points)
- sharpe: mean(returns) / stdev(returns) * sqrt(periods per year); 0.0 when
  volatility is zero (risk-free rate treated as zero)
- max_drawdown: min(equity / running peak - 1), <= 0
- turnover: mean over post-initial fill sessions of (session notional / 2) / equity
- avg_holding_days: mean span length between a symbol entering and leaving the
  portfolio (open positions measured to the final session)
"""

from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from statistics import mean, stdev
from typing import Any

from retailtrader.domain import (
    EvaluationMetrics,
    EvaluationReport,
    FidelityMetrics,
)

DAYS_PER_YEAR = 365.25


class InsufficientHistoryError(ValueError):
    """Not enough history to compute the metric."""


@dataclass(frozen=True)
class EquityPoint:
    session: date
    equity: float
    synthetic_mega_cap_proxy_equity: float
    equal_weight_equity: float


def read_equity_csv(path: Path) -> list[EquityPoint]:
    with path.open(encoding="utf-8") as handle:
        return [
            EquityPoint(
                session=date.fromisoformat(row["date"]),
                equity=float(row["equity"]),
                synthetic_mega_cap_proxy_equity=float(
                    row["synthetic_mega_cap_proxy_equity"]
                ),
                equal_weight_equity=float(row["equal_weight_equity"]),
            )
            for row in csv.DictReader(handle)
        ]


def _require_points(values: Sequence[float], minimum: int) -> None:
    if len(values) < minimum:
        raise InsufficientHistoryError(
            f"need at least {minimum} equity points, got {len(values)}"
        )


def _returns(values: Sequence[float]) -> list[float]:
    return [values[i] / values[i - 1] - 1 for i in range(1, len(values))]


def total_return(values: Sequence[float]) -> float:
    _require_points(values, 2)
    return values[-1] / values[0] - 1


def cagr(values: Sequence[float], sessions: Sequence[date]) -> float:
    _require_points(values, 2)
    days = (sessions[-1] - sessions[0]).days
    if days <= 0:
        raise InsufficientHistoryError("equity span must cover at least one day")
    return (values[-1] / values[0]) ** (DAYS_PER_YEAR / days) - 1


def _periods_per_year(sessions: Sequence[date]) -> float:
    spacing = (sessions[-1] - sessions[0]).days / (len(sessions) - 1)
    return DAYS_PER_YEAR / spacing


def annualized_volatility(values: Sequence[float], sessions: Sequence[date]) -> float:
    _require_points(values, 3)
    return stdev(_returns(values)) * _periods_per_year(sessions) ** 0.5


def sharpe_ratio(values: Sequence[float], sessions: Sequence[date]) -> float:
    _require_points(values, 3)
    returns = _returns(values)
    deviation = stdev(returns)
    if deviation == 0:
        return 0.0
    return mean(returns) / deviation * _periods_per_year(sessions) ** 0.5


def max_drawdown(values: Sequence[float]) -> float:
    _require_points(values, 2)
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def turnover(fills: Sequence[Mapping[str, Any]], points: Sequence[EquityPoint]) -> float:
    equity_by_session = {point.session: point.equity for point in points}
    notional_by_session: dict[date, float] = {}
    for fill in fills:
        session = datetime.fromisoformat(fill["filled_at"]).date()
        notional = fill["quantity"] * float(Decimal(fill["fill_price"]))
        notional_by_session[session] = notional_by_session.get(session, 0.0) + notional
    sessions = sorted(notional_by_session)[1:]  # skip initial funding buys
    if not sessions:
        return 0.0
    return mean(
        notional_by_session[session] / 2 / equity_by_session[session] for session in sessions
    )


def avg_holding_days(portfolios: Sequence[Mapping[str, Any]]) -> float:
    if not portfolios:
        return 0.0
    sessions = [datetime.fromisoformat(row["as_of"]).date() for row in portfolios]
    holdings = [{item["symbol"] for item in row["positions"]} for row in portfolios]
    spans: list[int] = []
    open_since: dict[str, date] = {}
    for session, held in zip(sessions, holdings):
        for symbol in sorted(held - open_since.keys()):
            open_since[symbol] = session
        for symbol in sorted(open_since.keys() - held):
            spans.append((session - open_since.pop(symbol)).days)
    spans.extend((sessions[-1] - entered).days for entered in open_since.values())
    return mean(spans) if spans else 0.0


def cash_exposure(portfolios: Sequence[Mapping[str, Any]]) -> float:
    if not portfolios:
        return 0.0
    return mean(
        float(Decimal(row["cash"])) / float(Decimal(row["total_equity"])) for row in portfolios
    )


def max_concentration(portfolios: Sequence[Mapping[str, Any]]) -> float:
    weights = [
        float(Decimal(item["value"])) / float(Decimal(row["total_equity"]))
        for row in portfolios
        for item in row["positions"]
    ]
    return max(weights, default=0.0)


def factor_coverage(decisions: Sequence[Mapping[str, Any]]) -> float:
    values = [
        factor.get("value")
        for record in decisions
        for selection in record.get("selected", [])
        for factor in selection.get("factors", [])
    ]
    if not values:
        return 1.0
    return sum(value is not None for value in values) / len(values)


def _selection_sets(decisions: Sequence[Mapping[str, Any]]) -> list[set[str]]:
    return [
        {selection["symbol"] for selection in record.get("selected", [])}
        for record in decisions
    ]


def ranking_churn(decisions: Sequence[Mapping[str, Any]]) -> float:
    """Mean fraction of the selection replaced between consecutive rebalances."""
    sets = _selection_sets(decisions)
    if len(sets) < 2:
        return 0.0
    return mean(
        1 - len(current & previous) / len(current) if current else 0.0
        for previous, current in zip(sets, sets[1:])
    )


def selection_stability(decisions: Sequence[Mapping[str, Any]]) -> float:
    """Mean Jaccard overlap of consecutive selections."""
    sets = _selection_sets(decisions)
    if len(sets) < 2:
        return 1.0
    return mean(
        len(current & previous) / len(current | previous) if current | previous else 1.0
        for previous, current in zip(sets, sets[1:])
    )


def rule_violations(portfolios: Sequence[Mapping[str, Any]]) -> int:
    return sum(Decimal(row["cash"]) < 0 for row in portfolios)


def benchmark_metrics(
    *,
    values: Sequence[float],
    sessions: Sequence[date],
    synthetic_mega_cap_proxy_values: Sequence[float],
    equal_weight_values: Sequence[float],
) -> dict[str, float | None]:
    """Return-series metrics for a benchmark index.

    A benchmark has no orders, positions, or cash, so the portfolio-specific
    metrics are None rather than a misleading zero. The comparison view renders
    those cells as an em dash.
    """
    return {
        "total_return": round(total_return(values), 4),
        "cagr": round(cagr(values, sessions), 4),
        "volatility": round(annualized_volatility(values, sessions), 4),
        "sharpe": round(sharpe_ratio(values, sessions), 4),
        "max_drawdown": round(max_drawdown(values), 4),
        "turnover": None,
        "trade_count": None,
        "avg_holding_days": None,
        "cash_exposure": None,
        "max_concentration": None,
        "synthetic_mega_cap_proxy_relative": round(
            total_return(values) - total_return(synthetic_mega_cap_proxy_values), 4
        ),
        "equal_weight_relative": round(
            total_return(values) - total_return(equal_weight_values), 4
        ),
    }


def compute_evaluation(
    *,
    run_id: str,
    as_of: datetime,
    equity: Sequence[EquityPoint],
    fills: Sequence[Mapping[str, Any]],
    portfolios: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    constraint_interventions: int = 0,
) -> EvaluationReport:
    values = [point.equity for point in equity]
    sessions = [point.session for point in equity]
    proxy_values = [point.synthetic_mega_cap_proxy_equity for point in equity]
    equal_weight_values = [point.equal_weight_equity for point in equity]

    metrics = EvaluationMetrics(
        total_return=total_return(values),
        cagr=cagr(values, sessions),
        volatility=annualized_volatility(values, sessions),
        sharpe=sharpe_ratio(values, sessions),
        max_drawdown=max_drawdown(values),
        turnover=turnover(fills, equity),
        trade_count=len(fills),
        avg_holding_days=avg_holding_days(portfolios),
        cash_exposure=cash_exposure(portfolios),
        max_concentration=max_concentration(portfolios),
        synthetic_mega_cap_proxy_relative=(
            total_return(values) - total_return(proxy_values)
        ),
        equal_weight_relative=total_return(values) - total_return(equal_weight_values),
    )
    fidelity = FidelityMetrics(
        factor_coverage=factor_coverage(decisions),
        constraint_interventions=constraint_interventions,
        ranking_churn=ranking_churn(decisions),
        selection_stability=selection_stability(decisions),
        rule_violations=rule_violations(portfolios),
    )
    return EvaluationReport(run_id=run_id, as_of=as_of, metrics=metrics, fidelity=fidelity)
