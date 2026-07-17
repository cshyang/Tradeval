"""Deterministic price-plus-quality candidate screening."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from retailtrader.agent.contracts import (
    Candidate,
    CandidateExclusion,
    CandidateSet,
    EvidenceMetric as ContractEvidenceMetric,
    MandateSpec,
    canonical_hash,
)
from retailtrader.agent.evidence import EvidenceMetric, derive_evidence
from retailtrader.data.cache import CachedDailyPriceSource, PriceCache
from retailtrader.data.fundamental_cache import FundamentalCache
from retailtrader.data.openbb import OpenBBYFinancePriceSource
from retailtrader.data.protocol import PriceQuery
from retailtrader.data.sec import LARGE_CAP_CIKS, SecCompanyFactsClient
from retailtrader.storage.events import replace_complete

_REQUIRED_METRICS = (
    "revenue_growth",
    "free_cash_flow_margin",
    "return_on_assets",
    "debt_to_assets",
    "earnings_consistency",
    "price_to_free_cash_flow",
)
_SCORE_SCALE = Decimal("0.000001")


@dataclass(frozen=True)
class ScreeningInput:
    symbol: str
    supported_security: bool
    price_history_sessions: int
    average_dollar_volume: Decimal
    latest_price: Decimal | None
    metrics: tuple[EvidenceMetric, ...]

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise ValueError("screening input symbol must be nonempty")
        if self.price_history_sessions < 0:
            raise ValueError("price_history_sessions must be non-negative")
        if self.average_dollar_volume < 0:
            raise ValueError("average_dollar_volume must be non-negative")
        if self.latest_price is not None and self.latest_price <= 0:
            raise ValueError("latest_price must be positive")
        names = [metric.name for metric in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate evidence metric for {symbol}")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "metrics", tuple(self.metrics))


def _utc_second(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("decision_at must be timezone-aware")
    value = value.astimezone(UTC)
    if value.microsecond:
        raise ValueError("decision_at must use whole seconds")
    return value.isoformat().replace("+00:00", "Z")


def _coverage(record: ScreeningInput | None) -> float:
    if record is None:
        return 0.0
    available = {
        metric.name
        for metric in record.metrics
        if metric.name in _REQUIRED_METRICS and metric.value is not None
    }
    return len(available) / len(_REQUIRED_METRICS)


def _evidence_refs(record: ScreeningInput | None) -> tuple[str, ...]:
    if record is None:
        return ()
    return tuple(
        sorted(
            {
                source_id
                for metric in record.metrics
                for source_id in metric.source_observation_ids
            }
        )
    )


def _score(record: ScreeningInput) -> float:
    values = {
        metric.name: metric.value
        for metric in record.metrics
        if metric.value is not None
    }
    price_to_fcf = values.get("price_to_free_cash_flow", Decimal(0))
    score = (
        values.get("revenue_growth", Decimal(0))
        + values.get("free_cash_flow_margin", Decimal(0))
        + values.get("return_on_assets", Decimal(0))
        - values.get("debt_to_assets", Decimal(0))
        + values.get("earnings_consistency", Decimal(0))
        - min(max(price_to_fcf, Decimal(0)), Decimal(100)) / Decimal(100)
    )
    return float(score.quantize(_SCORE_SCALE, rounding=ROUND_HALF_EVEN))


def _contract_metrics(record: ScreeningInput) -> tuple[ContractEvidenceMetric, ...]:
    return tuple(
        ContractEvidenceMetric(
            name=metric.name,
            value=None if metric.value is None else float(metric.value),
            unavailable_reason=metric.unavailable_reason,
            evidence_refs=metric.source_observation_ids,
            formula_version=metric.formula_version,
            decision_cutoff=_utc_second(metric.decision_cutoff),
        )
        for metric in sorted(record.metrics, key=lambda item: item.name)
    )


def _exclusion(
    symbol: str, reason: str, record: ScreeningInput | None
) -> CandidateExclusion:
    return CandidateExclusion(
        symbol=symbol,
        reason=reason,
        evidence_coverage=_coverage(record),
        evidence_refs=_evidence_refs(record),
    )


def screen_candidates(
    mandate: MandateSpec,
    decision_at: datetime,
    inputs: tuple[ScreeningInput, ...],
    market_data_hash: str,
) -> CandidateSet:
    """Filter a frozen universe, then rank non-pinned eligible securities."""
    decision_timestamp = _utc_second(decision_at)
    records: dict[str, ScreeningInput] = {}
    universe = set(mandate.universe.symbols)
    for record in inputs:
        if record.symbol not in universe:
            raise ValueError(f"screening input {record.symbol} is outside mandate universe")
        if record.symbol in records:
            raise ValueError(f"duplicate screening input: {record.symbol}")
        records[record.symbol] = record

    pinned = set(mandate.universe.pinned_symbols)
    excluded = set(mandate.universe.excluded_symbols)
    eligible: dict[str, Candidate] = {}
    exclusions: list[CandidateExclusion] = []
    minimum_dollar_volume = Decimal(mandate.universe.minimum_average_dollar_volume)

    for symbol in mandate.universe.symbols:
        record = records.get(symbol)
        if symbol in excluded:
            exclusions.append(_exclusion(symbol, "excluded by mandate", record))
            continue
        if record is None:
            exclusions.append(_exclusion(symbol, "missing screening input", None))
            continue
        if not record.supported_security:
            exclusions.append(_exclusion(symbol, "unsupported security", record))
            continue
        if record.latest_price is None:
            exclusions.append(_exclusion(symbol, "missing valid decision price", record))
            continue
        if any(metric.decision_cutoff > decision_at for metric in record.metrics):
            exclusions.append(_exclusion(symbol, "evidence is after decision cutoff", record))
            continue
        if symbol not in pinned:
            if record.price_history_sessions < mandate.universe.minimum_history_sessions:
                exclusions.append(_exclusion(symbol, "price history below minimum", record))
                continue
            if record.average_dollar_volume < minimum_dollar_volume:
                exclusions.append(
                    _exclusion(symbol, "average dollar volume below minimum", record)
                )
                continue
            if _coverage(record) < mandate.universe.minimum_evidence_coverage:
                exclusions.append(_exclusion(symbol, "evidence coverage below minimum", record))
                continue
        eligible[symbol] = Candidate(
            symbol=symbol,
            score=_score(record),
            evidence_coverage=_coverage(record),
            price_history_sessions=record.price_history_sessions,
            average_dollar_volume=format(record.average_dollar_volume, "f"),
            latest_price=format(record.latest_price, "f"),
            metrics=_contract_metrics(record),
        )

    pinned_candidates = [
        eligible[symbol]
        for symbol in mandate.universe.pinned_symbols
        if symbol in eligible
    ]
    ranked = sorted(
        (candidate for symbol, candidate in eligible.items() if symbol not in pinned),
        key=lambda candidate: (-candidate.score, candidate.symbol),
    )
    selected = tuple(
        [*pinned_candidates, *ranked][: mandate.universe.max_candidates]
    )
    selected_symbols = {candidate.symbol for candidate in selected}
    for symbol in sorted(set(eligible) - selected_symbols):
        exclusions.append(_exclusion(symbol, "below candidate rank cutoff", records[symbol]))

    payload = {
        "schema_version": 1,
        "experiment_id": mandate.experiment_id,
        "screener": "price_quality_v1",
        "decision_at": decision_timestamp,
        "market_data_hash": market_data_hash,
        "candidates": [candidate.model_dump(mode="json") for candidate in selected],
        "exclusions": [
            item.model_dump(mode="json")
            for item in sorted(exclusions, key=lambda exclusion: exclusion.symbol)
        ],
    }
    return CandidateSet.model_validate(
        payload | {"candidate_set_hash": canonical_hash(payload)}
    )


def prepare_screening_inputs(
    mandate: MandateSpec,
    decision_at: datetime,
    *,
    price_cache_root: Path,
    fundamental_cache_root: Path,
    sec_user_agent: str | None = None,
) -> tuple[tuple[ScreeningInput, ...], str]:
    """Load price and SEC evidence needed by the pure screener."""
    _utc_second(decision_at)
    supported_symbols = tuple(
        symbol for symbol in mandate.universe.symbols if symbol in LARGE_CAP_CIKS
    )
    if not supported_symbols:
        raise ValueError("mandate contains no supported US large-cap symbols")
    cutoff = decision_at.astimezone(UTC)
    query = PriceQuery(
        supported_symbols,
        (cutoff - timedelta(days=550)).date(),
        cutoff.date(),
    )
    price_result = CachedDailyPriceSource(
        OpenBBYFinancePriceSource(), PriceCache(price_cache_root)
    ).fetch(query)
    by_symbol = {
        symbol: tuple(
            observation
            for observation in price_result.batch.observations
            if observation.bar.symbol == symbol
            and observation.close_available_at <= cutoff
        )
        for symbol in supported_symbols
    }
    fundamental_cache = FundamentalCache(fundamental_cache_root)
    sec_client = (
        SecCompanyFactsClient(user_agent=sec_user_agent) if sec_user_agent else None
    )
    records: list[ScreeningInput] = []
    for symbol in mandate.universe.symbols:
        if symbol not in LARGE_CAP_CIKS:
            records.append(
                ScreeningInput(
                    symbol=symbol,
                    supported_security=False,
                    price_history_sessions=0,
                    average_dollar_volume=Decimal(0),
                    latest_price=None,
                    metrics=(),
                )
            )
            continue
        prices = tuple(sorted(by_symbol[symbol], key=lambda item: item.bar.session))
        latest = prices[-1] if prices else None
        cik = LARGE_CAP_CIKS[symbol]
        facts = fundamental_cache.load(cik)
        if facts is None and sec_client is not None:
            facts = sec_client.fetch(cik)
            fundamental_cache.store(facts)
        observations = () if facts is None else facts.observations
        derived = derive_evidence(
            observations,
            cutoff,
            price=None if latest is None else latest.bar.close,
            price_observation_id=None if latest is None else latest.source_ref,
        )
        average_dollar_volume = (
            sum(
                (item.bar.close * Decimal(item.bar.volume) for item in prices),
                Decimal(0),
            )
            / Decimal(len(prices))
            if prices
            else Decimal(0)
        )
        records.append(
            ScreeningInput(
                symbol=symbol,
                supported_security=True,
                price_history_sessions=len(prices),
                average_dollar_volume=average_dollar_volume,
                latest_price=None if latest is None else latest.bar.close,
                metrics=derived,
            )
        )
    return tuple(records), f"sha256:{price_result.batch.normalized_hash}"


def write_candidate_set(candidate_set: CandidateSet, path: Path) -> None:
    content = (
        json.dumps(
            candidate_set.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()
    replace_complete(path, content)
