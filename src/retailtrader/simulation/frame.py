"""Point-in-time inputs for one prior-close/next-open simulation transition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from retailtrader.domain import MarketSnapshot


@dataclass(frozen=True)
class SimulationFrame:
    """A decision snapshot paired with the later session used for execution.

    Signals are observed at ``decision.as_of``. Orders fill at ``execution_at``
    and the resulting portfolio is marked at ``execution.as_of``.
    """

    decision: MarketSnapshot
    execution: MarketSnapshot
    execution_at: datetime

    def __post_init__(self) -> None:
        timestamps = {
            "decision.as_of": self.decision.as_of,
            "execution_at": self.execution_at,
            "execution.as_of": self.execution.as_of,
        }
        naive = [
            name
            for name, value in timestamps.items()
            if value.tzinfo is None or value.utcoffset() is None
        ]
        if naive:
            raise ValueError(f"{', '.join(naive)} must be timezone-aware")
        if not self.decision.as_of < self.execution_at < self.execution.as_of:
            raise ValueError(
                "frame timestamps must satisfy decision.as_of < execution_at < execution.as_of"
            )
        decision_sessions = [bar.session for bar in self.decision.bars]
        execution_sessions = [bar.session for bar in self.execution.bars]
        if (
            decision_sessions
            and execution_sessions
            and not (max(decision_sessions) < min(execution_sessions))
        ):
            raise ValueError(
                "all decision bar sessions must be strictly before all execution bar sessions"
            )

    @property
    def execution_session(self) -> date:
        """Session key used by idempotency and benchmarks."""
        return self.execution.as_of.date()
