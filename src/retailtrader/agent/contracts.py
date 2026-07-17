"""Frozen wire contracts shared with the AgentTrader orchestration service."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)
from typing_extensions import Self

HashString = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
Symbol = Annotated[str, Field(min_length=1, max_length=32)]
UnitFloat = Annotated[float, Field(ge=0, le=1)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]


def _utc_second(value: Any) -> Any:
    if not isinstance(value, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ) is None:
        raise ValueError("timestamp must be an ISO UTC datetime with whole seconds")
    return value


UtcSecond = Annotated[AwareDatetime, BeforeValidator(_utc_second)]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Decision(FrozenModel):
    symbol: Symbol
    stance: Literal["buy", "hold", "sell"]
    confidence: UnitFloat
    desired_weight: UnitFloat
    thesis: Annotated[str, Field(min_length=1)]
    evidence_refs: tuple[Annotated[str, Field(min_length=1)], ...]
    risks: tuple[Annotated[str, Field(min_length=1)], ...]
    invalidating_conditions: tuple[Annotated[str, Field(min_length=1)], ...]
    intended_holding_period: Annotated[str, Field(min_length=1)]


class Abstention(FrozenModel):
    symbol: Symbol
    reason: Annotated[str, Field(min_length=1)]
    evidence_refs: tuple[Annotated[str, Field(min_length=1)], ...]


class DecisionProposal(FrozenModel):
    schema_version: Literal[1]
    experiment_id: Annotated[str, Field(min_length=1)]
    decision_at: UtcSecond
    candidate_set_hash: HashString
    agent_protocol_hash: HashString
    decisions: tuple[Decision, ...]
    abstentions: tuple[Abstention, ...]

    @model_validator(mode="after")
    def _unique_symbols(self) -> Self:
        symbols = [item.symbol for item in (*self.decisions, *self.abstentions)]
        duplicate = next(
            (symbol for index, symbol in enumerate(symbols) if symbol in symbols[:index]),
            None,
        )
        if duplicate is not None:
            raise ValueError(f"duplicate symbol: {duplicate}")
        return self


class CapitalSpec(FrozenModel):
    currency: Literal["USD"]
    initial_cash: Annotated[str, Field(pattern=r"^[0-9]+(?:\.[0-9]{1,2})?$")]


class UniverseSpec(FrozenModel):
    symbols: tuple[Symbol, ...]
    screener: Literal["price_quality_v1"]
    max_candidates: PositiveInt
    minimum_history_sessions: PositiveInt
    minimum_average_dollar_volume: Annotated[
        str, Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    ]
    minimum_evidence_coverage: UnitFloat
    pinned_symbols: tuple[Symbol, ...]
    excluded_symbols: tuple[Symbol, ...]

    @model_validator(mode="after")
    def _valid_overrides(self) -> Self:
        if not self.symbols:
            raise ValueError("universe symbols must not be empty")
        for label, symbols in (
            ("symbols", self.symbols),
            ("pinned_symbols", self.pinned_symbols),
            ("excluded_symbols", self.excluded_symbols),
        ):
            if len(symbols) != len(set(symbols)):
                raise ValueError(f"{label} contains duplicate symbols")
        overlap = set(self.pinned_symbols) & set(self.excluded_symbols)
        if overlap:
            raise ValueError(f"symbols cannot be both pinned and excluded: {sorted(overlap)[0]}")
        outside = (set(self.pinned_symbols) | set(self.excluded_symbols)) - set(self.symbols)
        if outside:
            raise ValueError(f"override symbol is outside universe: {sorted(outside)[0]}")
        if len(self.pinned_symbols) > self.max_candidates:
            raise ValueError("max_candidates must accommodate every pinned symbol")
        return self


class HorizonSpec(FrozenModel):
    kind: Literal["hindsight", "forward"]
    start: date
    end: date | None


class LimitSpec(FrozenModel):
    minimum_cash_weight: UnitFloat
    maximum_position_weight: Annotated[float, Field(gt=0, le=1)]
    maximum_turnover: UnitFloat
    maximum_drawdown: UnitFloat


class MandateSpec(FrozenModel):
    schema_version: Literal[1]
    experiment_id: Annotated[str, Field(min_length=1)]
    capital: CapitalSpec
    market: Literal["US"]
    universe: UniverseSpec
    cadence: Literal["weekly", "monthly"]
    horizon: HorizonSpec
    limits: LimitSpec


class SamplingSpec(FrozenModel):
    temperature: Annotated[float, Field(ge=0)]
    max_tokens: PositiveInt


class AgentProtocol(FrozenModel):
    schema_version: Literal[1]
    provider: Annotated[str, Field(min_length=1)]
    model_id: Annotated[str, Field(min_length=1)]
    system_prompt_hash: HashString
    recipe: Annotated[str, Field(min_length=1)]
    tools: tuple[Annotated[str, Field(min_length=1)], ...]
    sampling: SamplingSpec
    timeout_ms: PositiveInt
    retry_count: NonNegativeInt


class EvidenceMetric(FrozenModel):
    name: Annotated[str, Field(min_length=1)]
    value: float | None
    unavailable_reason: Annotated[str, Field(min_length=1)] | None
    evidence_refs: tuple[Annotated[str, Field(min_length=1)], ...]
    formula_version: Annotated[str, Field(min_length=1)]
    decision_cutoff: UtcSecond

    @model_validator(mode="after")
    def _value_or_reason(self) -> Self:
        if (self.value is None) == (self.unavailable_reason is None):
            raise ValueError("exactly one of value or unavailable_reason is required")
        return self


class Candidate(FrozenModel):
    symbol: Symbol
    score: float
    evidence_coverage: UnitFloat
    price_history_sessions: NonNegativeInt
    average_dollar_volume: Annotated[str, Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")]
    latest_price: Annotated[str, Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")]
    metrics: tuple[EvidenceMetric, ...]


class CandidateExclusion(FrozenModel):
    symbol: Symbol
    reason: Annotated[str, Field(min_length=1)]
    evidence_coverage: UnitFloat
    evidence_refs: tuple[Annotated[str, Field(min_length=1)], ...]


class CandidateSet(FrozenModel):
    schema_version: Literal[1]
    experiment_id: Annotated[str, Field(min_length=1)]
    screener: Literal["price_quality_v1"]
    decision_at: UtcSecond
    market_data_hash: HashString
    candidates: tuple[Candidate, ...]
    exclusions: tuple[CandidateExclusion, ...]
    candidate_set_hash: HashString


class AuditUsage(FrozenModel):
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt


class AgentAudit(FrozenModel):
    schema_version: Literal[1]
    job_id: Annotated[str, Field(min_length=1)]
    experiment_id: Annotated[str, Field(min_length=1)]
    operation: Literal["philosophy.generate", "proposal.generate"]
    provider: Annotated[str, Field(min_length=1)]
    model_id: Annotated[str, Field(min_length=1)]
    started_at: UtcSecond
    finished_at: UtcSecond
    input_hash: HashString
    output_hash: HashString
    usage: AuditUsage


def _canonical_number(value: float) -> str:
    if not math.isfinite(value):
        raise TypeError("canonical JSON does not support non-finite numbers")
    if value == 0:
        return "0"
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    text = repr(value)
    if "e" in text or "E" in text:
        mantissa, exponent = re.split("[eE]", text)
        if 1e-6 <= abs(value) < 1e21:
            return format(Decimal(text), "f")
        return f"{mantissa}e{int(exponent):+d}"
    return text


def canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        return canonical_json(value.model_dump(mode="python"))
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _canonical_number(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise TypeError("canonical JSON requires timezone-aware datetimes")
        timestamp = value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return json.dumps(timestamp)
    if isinstance(value, date):
        return json.dumps(value.isoformat())
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        entries = (
            f"{json.dumps(key, ensure_ascii=False)}:{canonical_json(value[key])}"
            for key in sorted(value)
        )
        return "{" + ",".join(entries) + "}"
    raise TypeError(f"canonical JSON does not support {type(value).__name__}")


def canonical_hash(value: Any) -> str:
    digest = hashlib.sha256(canonical_json(value).encode()).hexdigest()
    return f"sha256:{digest}"
