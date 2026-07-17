"""Build point-in-time weekly simulation frames from normalized daily prices."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from retailtrader.data.protocol import AvailableMarketBar, PriceBatch
from retailtrader.domain import MarketBar, MarketSnapshot
from retailtrader.simulation.frame import SimulationFrame

_NEW_YORK = ZoneInfo("America/New_York")
_CENT = Decimal("0.01")
MAX_REFERENCE_SESSION_GAP_DAYS = 7
REFERENCE_METHOD_VERSION = "execution_open_fixed_basket_v1"


def market_open_utc(session: date) -> datetime:
    """Canonical regular-session US equity open."""
    return datetime.combine(session, time(9, 30), tzinfo=_NEW_YORK).astimezone(UTC)


def market_close_utc(session: date) -> datetime:
    """Canonical regular-session US equity close (conservative on early closes)."""
    return datetime.combine(session, time(16), tzinfo=_NEW_YORK).astimezone(UTC)


def _symbols(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise TypeError("universe must be a non-string sequence")
    if any(not isinstance(value, str) for value in values):
        raise TypeError("universe symbols must be strings")
    normalized = tuple(sorted({value.strip().upper() for value in values if value.strip()}))
    if not normalized:
        raise ValueError("universe must contain at least one symbol")
    return normalized


def _observation_index(
    batch: PriceBatch,
) -> dict[tuple[str, date], AvailableMarketBar]:
    return {
        (observation.bar.symbol, observation.bar.session): observation
        for observation in batch.observations
    }


def weekly_session_pairs(
    reference_sessions: Sequence[date], start: date, end: date
) -> tuple[tuple[date, date], ...]:
    """Pair each week's last observed session with the next actual session.

    ``start`` and ``end`` constrain execution sessions. The supplied reference
    sessions are the calendar source; an implausibly long gap is treated as a
    provider/calendar error rather than silently stretching a holding period.
    """
    if not isinstance(start, date) or isinstance(start, datetime):
        raise TypeError("start must be a date")
    if not isinstance(end, date) or isinstance(end, datetime):
        raise TypeError("end must be a date")
    if start > end:
        raise ValueError("start must not be after end")
    sessions = sorted(set(reference_sessions))
    for previous, current in zip(sessions, sessions[1:], strict=False):
        if (current - previous).days > MAX_REFERENCE_SESSION_GAP_DAYS:
            raise ValueError(
                "reference calendar has an implausible session gap: "
                f"{previous} to {current}"
            )
    last_by_week: dict[tuple[int, int], date] = {}
    for session in sessions:
        iso = session.isocalendar()
        last_by_week[(iso.year, iso.week)] = session
    position = {session: index for index, session in enumerate(sessions)}
    pairs: list[tuple[date, date]] = []
    for decision in sorted(last_by_week.values()):
        index = position[decision]
        if index + 1 >= len(sessions):
            continue
        execution = sessions[index + 1]
        if start <= execution <= end:
            pairs.append((decision, execution))
    return tuple(pairs)


def _require_observations(
    index: dict[tuple[str, date], AvailableMarketBar],
    symbols: tuple[str, ...],
    session: date,
    stage: str,
) -> tuple[AvailableMarketBar, ...]:
    missing = [symbol for symbol in symbols if (symbol, session) not in index]
    if missing:
        raise ValueError(
            f"missing {stage} bars for {session}: {', '.join(sorted(missing))}"
        )
    return tuple(index[(symbol, session)] for symbol in symbols)


def build_price_frames(
    batch: PriceBatch,
    universe: Sequence[str],
    start: date,
    end: date,
    benchmark_symbol: str = "SPY",
) -> tuple[SimulationFrame, ...]:
    """Build decision-close/next-open frames after enforcing availability."""
    if not isinstance(batch, PriceBatch):
        raise TypeError("batch must be a PriceBatch")
    symbols = _symbols(universe)
    benchmark_symbol = benchmark_symbol.strip().upper()
    if not benchmark_symbol:
        raise ValueError("benchmark symbol must be nonempty")
    index = _observation_index(batch)
    reference_sessions = sorted(
        observation.bar.session
        for observation in batch.observations
        if observation.bar.symbol == benchmark_symbol
    )
    if not reference_sessions:
        raise ValueError(f"no reference calendar bars for {benchmark_symbol}")

    frames: list[SimulationFrame] = []
    for decision_session, execution_session in weekly_session_pairs(
        reference_sessions, start, end
    ):
        decision_as_of = market_close_utc(decision_session)
        execution_at = market_open_utc(execution_session)
        execution_as_of = market_close_utc(execution_session)
        decisions = _require_observations(
            index, symbols, decision_session, "decision"
        )
        executions = _require_observations(
            index, symbols, execution_session, "execution"
        )
        for observation in decisions:
            if observation.bar.session > decision_session:
                raise ValueError("future-session decision bar rejected")
            if observation.close_available_at > decision_as_of:
                raise ValueError(
                    f"decision bar unavailable at decision close: {observation.source_ref}"
                )
        for observation in executions:
            if observation.open_available_at > execution_at:
                raise ValueError(
                    f"execution open unavailable at fill time: {observation.source_ref}"
                )
            if observation.close_available_at > execution_as_of:
                raise ValueError(
                    f"execution close unavailable at mark time: {observation.source_ref}"
                )
        decision_snapshot = MarketSnapshot(
            as_of=decision_as_of,
            bars=tuple(observation.bar for observation in decisions),
        )
        execution_snapshot = MarketSnapshot(
            as_of=execution_as_of,
            bars=tuple(observation.bar for observation in executions),
        )
        frames.append(
            SimulationFrame(
                decision=decision_snapshot,
                execution=execution_snapshot,
                execution_at=execution_at,
            )
        )
    return tuple(frames)


def history_as_of(
    batch: PriceBatch,
    universe: Sequence[str],
    decision_as_of: datetime,
) -> dict[str, tuple[MarketBar, ...]]:
    """Return only completed bars from sessions admitted by a decision time."""
    if not isinstance(decision_as_of, datetime) or decision_as_of.tzinfo is None:
        raise ValueError("decision_as_of must be timezone-aware")
    decision_as_of = decision_as_of.astimezone(UTC)
    decision_session = decision_as_of.astimezone(_NEW_YORK).date()
    symbols = _symbols(universe)
    grouped: dict[str, list[MarketBar]] = {symbol: [] for symbol in symbols}
    for observation in batch.observations:
        symbol = observation.bar.symbol
        if symbol not in grouped:
            continue
        if observation.bar.session > decision_session:
            continue
        if observation.close_available_at > decision_as_of:
            continue
        grouped[symbol].append(observation.bar)
    return {
        symbol: tuple(sorted(bars, key=lambda bar: bar.session))
        for symbol, bars in grouped.items()
    }


def build_reference_indices(
    frames: Sequence[SimulationFrame],
    batch: PriceBatch,
    universe: Sequence[str],
    initial_cash: Decimal,
    benchmark_symbol: str = "SPY",
) -> dict[date, tuple[Decimal, Decimal]]:
    """No-cost fractional SPY and fixed equal-weight buy-and-hold references."""
    if not frames:
        raise ValueError("at least one simulation frame is required")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    symbols = _symbols(universe)
    benchmark_symbol = benchmark_symbol.strip().upper()
    ordered_frames = tuple(sorted(frames, key=lambda frame: frame.execution_session))
    sessions = [frame.execution_session for frame in ordered_frames]
    if len(sessions) != len(set(sessions)):
        raise ValueError("reference frames contain duplicate execution sessions")
    index = _observation_index(batch)
    first_frame = ordered_frames[0]
    first_session = first_frame.execution_session
    benchmark_baseline = _require_observations(
        index, (benchmark_symbol,), first_session, "reference baseline"
    )[0]
    universe_baseline = _require_observations(
        index, symbols, first_session, "reference baseline"
    )
    for observation in (benchmark_baseline, *universe_baseline):
        if observation.open_available_at > first_frame.execution_at:
            raise ValueError(
                f"reference open unavailable at baseline: {observation.source_ref}"
            )
    benchmark_base = benchmark_baseline.bar.open
    universe_base = {
        observation.bar.symbol: observation.bar.open
        for observation in universe_baseline
    }
    allocation = initial_cash / Decimal(len(symbols))
    references: dict[date, tuple[Decimal, Decimal]] = {}
    for frame in ordered_frames:
        session = frame.execution_session
        benchmark_mark = _require_observations(
            index, (benchmark_symbol,), session, "reference mark"
        )[0]
        universe_marks = _require_observations(
            index, symbols, session, "reference mark"
        )
        for observation in (benchmark_mark, *universe_marks):
            if observation.close_available_at > frame.execution.as_of:
                raise ValueError(
                    f"reference close unavailable at mark: {observation.source_ref}"
                )
        benchmark_close = benchmark_mark.bar.close
        strategy_closes = {
            observation.bar.symbol: observation.bar.close
            for observation in universe_marks
        }
        benchmark_equity = (
            initial_cash * benchmark_close / benchmark_base
        ).quantize(_CENT, rounding=ROUND_HALF_UP)
        equal_weight_equity = sum(
            (
                allocation * strategy_closes[symbol] / universe_base[symbol]
                for symbol in symbols
            ),
            Decimal(0),
        ).quantize(_CENT, rounding=ROUND_HALF_UP)
        references[session] = (benchmark_equity, equal_weight_equity)
    return references
