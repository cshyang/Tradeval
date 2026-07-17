"""Derive point-in-time quality evidence from availability-bearing SEC facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal

from retailtrader.data.sec import FundamentalObservation

_SCALE = Decimal("0.000001")


@dataclass(frozen=True)
class EvidenceMetric:
    name: str
    value: Decimal | None
    source_observation_ids: tuple[str, ...]
    formula_version: str
    decision_cutoff: datetime
    unavailable_reason: str | None

    def __post_init__(self) -> None:
        if self.decision_cutoff.tzinfo is None or self.decision_cutoff.utcoffset() is None:
            raise ValueError("decision_cutoff must be timezone-aware")
        if (self.value is None) == (self.unavailable_reason is None):
            raise ValueError("exactly one of value or unavailable_reason is required")


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_SCALE, rounding=ROUND_HALF_EVEN)


def _metric(
    name: str,
    formula_version: str,
    cutoff: datetime,
    *,
    value: Decimal | None = None,
    sources: tuple[FundamentalObservation, ...] = (),
    extra_source_ids: tuple[str, ...] = (),
    unavailable_reason: str | None = None,
) -> EvidenceMetric:
    return EvidenceMetric(
        name=name,
        value=None if value is None else _quantize(value),
        source_observation_ids=tuple(item.observation_id for item in sources)
        + extra_source_ids,
        formula_version=formula_version,
        decision_cutoff=cutoff,
        unavailable_reason=unavailable_reason,
    )


def _latest_by_year(
    observations: tuple[FundamentalObservation, ...], cutoff: datetime
) -> dict[str, dict[int, FundamentalObservation]]:
    selected: dict[str, dict[int, FundamentalObservation]] = {}
    for observation in observations:
        if observation.available_at > cutoff:
            continue
        years = selected.setdefault(observation.metric, {})
        current = years.get(observation.fiscal_year)
        if current is None or (observation.available_at, observation.accession) > (
            current.available_at,
            current.accession,
        ):
            years[observation.fiscal_year] = observation
    return selected


def _common_latest(
    by_metric: dict[str, dict[int, FundamentalObservation]], *metrics: str
) -> tuple[FundamentalObservation, ...] | None:
    common_years = set.intersection(
        *(set(by_metric.get(metric, {})) for metric in metrics)
    )
    if not common_years:
        return None
    year = max(common_years)
    return tuple(by_metric[metric][year] for metric in metrics)


def derive_evidence(
    observations: tuple[FundamentalObservation, ...],
    decision_cutoff: datetime,
    *,
    price: Decimal | None = None,
    price_observation_id: str | None = None,
) -> tuple[EvidenceMetric, ...]:
    """Calculate the v1 quality metric set using only facts available by cutoff."""
    if decision_cutoff.tzinfo is None or decision_cutoff.utcoffset() is None:
        raise ValueError("decision_cutoff must be timezone-aware")
    cutoff = decision_cutoff.astimezone(UTC)
    by_metric = _latest_by_year(tuple(observations), cutoff)
    metrics: list[EvidenceMetric] = []

    revenues = by_metric.get("revenue", {})
    revenue_years = sorted(revenues, reverse=True)
    if len(revenue_years) >= 2:
        current, previous = (revenues[year] for year in revenue_years[:2])
        metrics.append(
            _metric(
                "revenue_growth",
                "revenue_growth_yoy_v1",
                cutoff,
                value=current.value / previous.value - 1,
                sources=(current, previous),
            )
        )
    else:
        metrics.append(
            _metric(
                "revenue_growth",
                "revenue_growth_yoy_v1",
                cutoff,
                unavailable_reason="requires two available annual revenue facts",
            )
        )

    fcf_inputs = _common_latest(
        by_metric, "revenue", "operating_cash_flow", "capital_expenditure"
    )
    if fcf_inputs is not None and fcf_inputs[0].value != 0:
        revenue, operating_cash_flow, capital_expenditure = fcf_inputs
        free_cash_flow = operating_cash_flow.value - capital_expenditure.value
        metrics.append(
            _metric(
                "free_cash_flow_margin",
                "fcf_margin_v1",
                cutoff,
                value=free_cash_flow / revenue.value,
                sources=fcf_inputs,
            )
        )
    else:
        metrics.append(
            _metric(
                "free_cash_flow_margin",
                "fcf_margin_v1",
                cutoff,
                unavailable_reason="requires aligned annual revenue, operating cash flow, and capex",
            )
        )

    income_assets = _common_latest(by_metric, "net_income", "assets")
    if income_assets is not None:
        net_income, current_assets = income_assets
        previous_assets = by_metric["assets"].get(current_assets.fiscal_year - 1)
    else:
        previous_assets = None
    if income_assets is not None and previous_assets is not None:
        average_assets = (current_assets.value + previous_assets.value) / 2
        metrics.append(
            _metric(
                "return_on_assets",
                "return_on_average_assets_v1",
                cutoff,
                value=net_income.value / average_assets,
                sources=(net_income, current_assets, previous_assets),
            )
        )
    else:
        metrics.append(
            _metric(
                "return_on_assets",
                "return_on_average_assets_v1",
                cutoff,
                unavailable_reason="requires annual net income and two annual asset balances",
            )
        )

    debt_assets = _common_latest(by_metric, "debt", "assets")
    if debt_assets is not None and debt_assets[1].value != 0:
        debt, assets = debt_assets
        metrics.append(
            _metric(
                "debt_to_assets",
                "debt_to_assets_v1",
                cutoff,
                value=debt.value / assets.value,
                sources=debt_assets,
            )
        )
    else:
        metrics.append(
            _metric(
                "debt_to_assets",
                "debt_to_assets_v1",
                cutoff,
                unavailable_reason="requires aligned annual debt and assets",
            )
        )

    income_years = sorted(by_metric.get("net_income", {}), reverse=True)[:3]
    if len(income_years) == 3:
        income_facts = tuple(by_metric["net_income"][year] for year in income_years)
        positive_years = sum(item.value > 0 for item in income_facts)
        metrics.append(
            _metric(
                "earnings_consistency",
                "positive_earnings_three_year_v1",
                cutoff,
                value=Decimal(positive_years) / Decimal(3),
                sources=income_facts,
            )
        )
    else:
        metrics.append(
            _metric(
                "earnings_consistency",
                "positive_earnings_three_year_v1",
                cutoff,
                unavailable_reason="requires three available annual net income facts",
            )
        )

    valuation_inputs = _common_latest(
        by_metric,
        "operating_cash_flow",
        "capital_expenditure",
        "diluted_shares",
    )
    if price is None:
        valuation_reason = "requires an available decision-time price"
    elif price <= 0:
        valuation_reason = "price must be positive"
    elif not price_observation_id:
        valuation_reason = "price observation ID is required"
    elif valuation_inputs is None:
        valuation_reason = "requires aligned annual cash flow, capex, and diluted shares"
    else:
        operating_cash_flow, capital_expenditure, diluted_shares = valuation_inputs
        free_cash_flow = operating_cash_flow.value - capital_expenditure.value
        if free_cash_flow <= 0:
            valuation_reason = "free cash flow must be positive"
        else:
            metrics.append(
                _metric(
                    "price_to_free_cash_flow",
                    "price_to_fcf_v1",
                    cutoff,
                    value=price * diluted_shares.value / free_cash_flow,
                    sources=valuation_inputs,
                    extra_source_ids=(price_observation_id,),
                )
            )
            valuation_reason = None
    if valuation_reason is not None:
        metrics.append(
            _metric(
                "price_to_free_cash_flow",
                "price_to_fcf_v1",
                cutoff,
                unavailable_reason=valuation_reason,
            )
        )

    return tuple(metrics)
