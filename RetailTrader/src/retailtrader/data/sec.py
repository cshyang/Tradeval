"""Point-in-time SEC EDGAR company-facts adapter."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.request import Request, urlopen

from retailtrader.data.protocol import canonical_json

COMPANYFACTS_ENDPOINT_VERSION = "sec-companyfacts-v1"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

LARGE_CAP_CIKS = {
    "AAPL": "0000320193",
    "ABBV": "0001551152",
    "ADBE": "0000796343",
    "AMD": "0000002488",
    "AMZN": "0001018724",
    "COST": "0000909832",
    "CRM": "0001108524",
    "CSCO": "0000858877",
    "CVX": "0000093410",
    "GOOGL": "0001652044",
    "HD": "0000354950",
    "INTC": "0000050863",
    "JNJ": "0000200406",
    "JPM": "0000019617",
    "KO": "0000021344",
    "LLY": "0000059478",
    "MA": "0001141391",
    "META": "0001326801",
    "MRK": "0000310158",
    "MSFT": "0000789019",
    "NFLX": "0001065280",
    "NVDA": "0001045810",
    "ORCL": "0001341439",
    "PEP": "0000077476",
    "PG": "0000080424",
    "TSLA": "0001318605",
    "UNH": "0000731766",
    "V": "0001403161",
    "WMT": "0000104169",
    "XOM": "0000034088",
}

_CONCEPTS = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
    ),
    "net_income": ("NetIncomeLoss",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capital_expenditure": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "debt": (
        "LongTermDebt",
        "LongTermDebtCurrent",
        "LongTermDebtAndFinanceLeaseObligationsCurrent",
    ),
    "diluted_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
}
_UNITS = {"diluted_shares": "shares"}
OpenUrl = Callable[..., Any]
Clock = Callable[[], datetime]


class SecDataError(RuntimeError):
    """SEC data is unavailable, malformed, or conflicts with immutable content."""


def normalize_cik(value: str | int) -> str:
    text = str(value).strip()
    if not text.isdigit() or len(text) > 10:
        raise ValueError(f"invalid SEC CIK: {value!r}")
    return text.zfill(10)


def _raw_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise SecDataError(f"{label} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SecDataError(f"{label} must be numeric") from exc
    if not result.is_finite():
        raise SecDataError(f"{label} must be finite")
    return result


@dataclass(frozen=True)
class FundamentalObservation:
    observation_id: str
    cik: str
    metric: str
    value: Decimal
    unit: str
    period_start: date | None
    period_end: date
    fiscal_year: int
    form: str
    filed_date: date
    available_at: datetime
    accession: str
    source_ref: str


def normalize_company_facts(payload: Mapping[str, Any]) -> tuple[FundamentalObservation, ...]:
    try:
        cik = normalize_cik(payload["cik"])
        us_gaap = payload["facts"]["us-gaap"]
    except (KeyError, TypeError, ValueError) as exc:
        raise SecDataError(f"invalid SEC companyfacts envelope: {exc}") from exc
    if not isinstance(us_gaap, Mapping):
        raise SecDataError("SEC us-gaap facts must be an object")

    observations: list[FundamentalObservation] = []
    for metric, aliases in _CONCEPTS.items():
        concept_name = next((name for name in aliases if name in us_gaap), None)
        if concept_name is None:
            continue
        concept = us_gaap[concept_name]
        unit = _UNITS.get(metric, "USD")
        try:
            rows = concept["units"][unit]
        except (KeyError, TypeError) as exc:
            raise SecDataError(f"{concept_name} has no {unit} facts") from exc
        if not isinstance(rows, list):
            raise SecDataError(f"{concept_name} {unit} facts must be a list")
        for row in rows:
            if not isinstance(row, Mapping) or row.get("fp") != "FY":
                continue
            if row.get("form") not in {"10-K", "10-K/A"}:
                continue
            if not row.get("filed"):
                raise SecDataError(f"{concept_name} annual fact is missing filed date")
            try:
                period_end = date.fromisoformat(str(row["end"]))
                filed_date = date.fromisoformat(str(row["filed"]))
                fiscal_year = int(row["fy"])
                accession = str(row["accn"])
                form = str(row["form"])
                period_start = (
                    date.fromisoformat(str(row["start"])) if row.get("start") else None
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise SecDataError(f"invalid {concept_name} annual fact: {exc}") from exc
            available_at = datetime.combine(
                filed_date + timedelta(days=1), time.min, tzinfo=UTC
            )
            observation_id = f"sec:{cik}:{metric}:{accession}:{period_end.isoformat()}"
            observations.append(
                FundamentalObservation(
                    observation_id=observation_id,
                    cik=cik,
                    metric=metric,
                    value=_decimal(row.get("val"), f"{concept_name}.val"),
                    unit=unit,
                    period_start=period_start,
                    period_end=period_end,
                    fiscal_year=fiscal_year,
                    form=form,
                    filed_date=filed_date,
                    available_at=available_at,
                    accession=accession,
                    source_ref=f"sec:companyfacts:{cik}:{accession}",
                )
            )
    return tuple(
        sorted(
            observations,
            key=lambda item: (item.fiscal_year, item.metric, item.available_at, item.accession),
        )
    )


def _normalized_hash(observations: tuple[FundamentalObservation, ...]) -> str:
    rows = [
        {
            "observation_id": item.observation_id,
            "cik": item.cik,
            "metric": item.metric,
            "value": item.value,
            "unit": item.unit,
            "period_start": item.period_start,
            "period_end": item.period_end,
            "fiscal_year": item.fiscal_year,
            "form": item.form,
            "filed_date": item.filed_date,
            "available_at": item.available_at,
            "accession": item.accession,
            "source_ref": item.source_ref,
        }
        for item in observations
    ]
    return hashlib.sha256(canonical_json(rows).encode()).hexdigest()


@dataclass(frozen=True)
class SecCompanyFacts:
    cik: str
    entity_name: str
    observations: tuple[FundamentalObservation, ...]
    retrieved_at: datetime
    raw_json: str
    raw_hash: str
    normalized_hash: str

    @classmethod
    def create(
        cls,
        *,
        cik: str | int,
        payload: Mapping[str, Any],
        retrieved_at: datetime,
    ) -> SecCompanyFacts:
        normalized_cik = normalize_cik(cik)
        payload_cik = normalize_cik(payload.get("cik", ""))
        if payload_cik != normalized_cik:
            raise SecDataError("SEC response CIK does not match request")
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise ValueError("SEC retrieval time must be timezone-aware")
        raw_json = _raw_json(payload)
        observations = normalize_company_facts(payload)
        entity_name = str(payload.get("entityName", "")).strip()
        if not entity_name:
            raise SecDataError("SEC response has no entityName")
        return cls(
            cik=normalized_cik,
            entity_name=entity_name,
            observations=observations,
            retrieved_at=retrieved_at.astimezone(UTC),
            raw_json=raw_json,
            raw_hash=hashlib.sha256(raw_json.encode()).hexdigest(),
            normalized_hash=_normalized_hash(observations),
        )


class SecCompanyFactsClient:
    """Fetch company facts directly from the official SEC JSON endpoint."""

    def __init__(
        self,
        *,
        user_agent: str,
        opener: OpenUrl = urlopen,
        clock: Clock = lambda: datetime.now(UTC),
        timeout: float = 30,
    ) -> None:
        if not user_agent.strip() or "@" not in user_agent:
            raise ValueError("SEC User-Agent must include an application name and contact email")
        self.user_agent = user_agent.strip()
        self.opener = opener
        self.clock = clock
        self.timeout = timeout

    def fetch(self, cik: str | int) -> SecCompanyFacts:
        normalized_cik = normalize_cik(cik)
        request = Request(
            COMPANYFACTS_URL.format(cik=normalized_cik),
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                body = response.read()
            payload = json.loads(body)
        except Exception as exc:
            raise SecDataError(f"SEC companyfacts request failed for {normalized_cik}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise SecDataError("SEC companyfacts response must be an object")
        return SecCompanyFacts.create(
            cik=normalized_cik,
            payload=payload,
            retrieved_at=self.clock(),
        )
