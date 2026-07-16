"""Frozen domain contracts shared by every build track.

Phase 0 contract freeze: worktree tracks build against these models and must
not modify this module. All datetimes are UTC-aware. Money is Decimal and
serializes as strings.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

SCHEMA_VERSION = 1
ENGINE_VERSION = "0.1.0"
WEIGHT_TOLERANCE = 1e-6


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def _utc_datetimes(cls, v: object) -> object:
        if isinstance(v, datetime):
            if v.tzinfo is None:
                raise ValueError("datetime must be timezone-aware")
            return v.astimezone(UTC)
        return v


class MarketBar(DomainModel):
    symbol: str
    session: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    @model_validator(mode="after")
    def _positive_prices(self) -> MarketBar:
        for name in ("open", "high", "low", "close"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.volume < 0:
            raise ValueError("volume must be non-negative")
        return self


class FundamentalObservation(DomainModel):
    symbol: str
    metric: str
    value: float
    period_end: date
    available_at: datetime
    as_of: datetime
    availability_source: Literal["provider", "approximated"] = "provider"

    @model_validator(mode="after")
    def _point_in_time(self) -> FundamentalObservation:
        if self.available_at > self.as_of:
            raise ValueError("observation not available at as_of (look-ahead)")
        return self


class MarketSnapshot(DomainModel):
    as_of: datetime
    bars: tuple[MarketBar, ...]
    fundamentals: tuple[FundamentalObservation, ...] = ()

    @model_validator(mode="after")
    def _unique_bar_symbols(self) -> MarketSnapshot:
        symbols = [b.symbol for b in self.bars]
        if len(symbols) != len(set(symbols)):
            raise ValueError("duplicate symbols in snapshot bars")
        return self


class FactorObservation(DomainModel):
    symbol: str
    metric: str
    value: float | None = None
    unavailable_reason: str | None = None
    formula_version: str
    source_refs: tuple[str, ...] = ()
    as_of: datetime

    @model_validator(mode="after")
    def _value_xor_reason(self) -> FactorObservation:
        if (self.value is None) == (self.unavailable_reason is None):
            raise ValueError("exactly one of value or unavailable_reason must be set")
        return self


class TargetPosition(DomainModel):
    symbol: str
    weight: float

    @model_validator(mode="after")
    def _long_only(self) -> TargetPosition:
        if not 0 < self.weight <= 1:
            raise ValueError("weight must be in (0, 1]")
        return self


class TargetPortfolio(DomainModel):
    run_id: str
    as_of: datetime
    cash_weight: float
    positions: tuple[TargetPosition, ...]

    @model_validator(mode="after")
    def _fully_invested_no_leverage(self) -> TargetPortfolio:
        if self.cash_weight < 0:
            raise ValueError("cash_weight must be non-negative")
        symbols = [p.symbol for p in self.positions]
        if len(symbols) != len(set(symbols)):
            raise ValueError("duplicate symbols in target portfolio")
        total = self.cash_weight + sum(p.weight for p in self.positions)
        if abs(total - 1.0) > WEIGHT_TOLERANCE:
            raise ValueError(f"weights plus cash must total 1.0, got {total}")
        return self


class OrderIntent(DomainModel):
    run_id: str
    as_of: datetime
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int

    @model_validator(mode="after")
    def _positive_quantity(self) -> OrderIntent:
        if self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        return self


class FillEvent(DomainModel):
    run_id: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    fill_price: Decimal
    filled_at: datetime

    @model_validator(mode="after")
    def _valid_fill(self) -> FillEvent:
        if self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        if self.fill_price <= 0:
            raise ValueError("fill_price must be positive")
        return self


class Position(DomainModel):
    symbol: str
    quantity: int
    price: Decimal
    value: Decimal

    @model_validator(mode="after")
    def _non_negative(self) -> Position:
        if self.quantity < 0:
            raise ValueError("quantity must be non-negative")
        if self.price <= 0 or self.value < 0:
            raise ValueError("price must be positive and value non-negative")
        return self


class PortfolioSnapshot(DomainModel):
    run_id: str
    as_of: datetime
    cash: Decimal
    positions: tuple[Position, ...]
    total_equity: Decimal

    @model_validator(mode="after")
    def _consistent(self) -> PortfolioSnapshot:
        if self.cash < 0:
            raise ValueError("cash must be non-negative")
        symbols = [p.symbol for p in self.positions]
        if len(symbols) != len(set(symbols)):
            raise ValueError("duplicate symbols in portfolio")
        return self


FilterOp = Literal["gt", "gte", "lt", "lte", "eq", "between"]


class EligibilityFilter(DomainModel):
    metric: str
    op: FilterOp
    value: float | tuple[float, float]

    @model_validator(mode="after")
    def _between_needs_pair(self) -> EligibilityFilter:
        if self.op == "between" and not isinstance(self.value, tuple):
            raise ValueError("between requires a (low, high) pair")
        if self.op != "between" and isinstance(self.value, tuple):
            raise ValueError(f"{self.op} requires a scalar value")
        return self


class PhilosophyFactor(DomainModel):
    name: str
    weight: float
    direction: Literal["higher_is_better", "lower_is_better"]

    @model_validator(mode="after")
    def _positive_weight(self) -> PhilosophyFactor:
        if self.weight <= 0:
            raise ValueError("factor weight must be positive")
        return self


class PhilosophySpec(DomainModel):
    name: str
    version: str
    universe: str
    cadence: Literal["weekly", "monthly"]
    filters: tuple[EligibilityFilter, ...] = ()
    factors: tuple[PhilosophyFactor, ...]
    min_factor_coverage: float
    top_n: int
    cash_buffer: float
    max_position_weight: float
    max_turnover: float | None = None
    content_hash: str | None = None

    @model_validator(mode="after")
    def _valid_spec(self) -> PhilosophySpec:
        if not self.factors:
            raise ValueError("at least one factor is required")
        names = [f.name for f in self.factors]
        if len(names) != len(set(names)):
            raise ValueError("duplicate factor names")
        for field in ("min_factor_coverage", "cash_buffer", "max_position_weight"):
            if not 0 <= getattr(self, field) <= 1:
                raise ValueError(f"{field} must be within [0, 1]")
        if self.top_n <= 0:
            raise ValueError("top_n must be positive")
        return self


class ExperimentManifest(DomainModel):
    id: str
    run_id: str
    schema_version: int = SCHEMA_VERSION
    philosophy_name: str
    philosophy_version: str
    philosophy_hash: str
    universe_hash: str
    engine_version: str = ENGINE_VERSION
    cadence: Literal["weekly", "monthly"]
    start: date
    end: date
    created_at: datetime

    @model_validator(mode="after")
    def _valid_window(self) -> ExperimentManifest:
        if self.start >= self.end:
            raise ValueError("start must precede end")
        return self


class EvaluationMetrics(DomainModel):
    total_return: float
    cagr: float
    volatility: float
    sharpe: float
    max_drawdown: float
    turnover: float
    trade_count: int
    avg_holding_days: float
    cash_exposure: float
    max_concentration: float
    spy_relative: float
    equal_weight_relative: float


class FidelityMetrics(DomainModel):
    factor_coverage: float
    constraint_interventions: int
    ranking_churn: float
    selection_stability: float
    rule_violations: int


class EvaluationReport(DomainModel):
    run_id: str
    as_of: datetime
    schema_version: int = SCHEMA_VERSION
    engine_version: str = ENGINE_VERSION
    metrics: EvaluationMetrics
    fidelity: FidelityMetrics
