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
        if not self.decision.bars:
            raise ValueError("decision snapshot must contain at least one bar")
        if not self.execution.bars:
            raise ValueError("execution snapshot must contain at least one bar")

        decision_sessions = {bar.session for bar in self.decision.bars}
        expected_decision_session = self.decision.as_of.date()
        if decision_sessions != {expected_decision_session}:
            raise ValueError(
                "all decision bars must share the decision.as_of session "
                f"{expected_decision_session.isoformat()}"
            )

        execution_sessions = {bar.session for bar in self.execution.bars}
        expected_execution_session = self.execution.as_of.date()
        if execution_sessions != {expected_execution_session}:
            raise ValueError(
                "all execution bars must share the execution.as_of session "
                f"{expected_execution_session.isoformat()}"
            )
        if self.execution_at.date() != expected_execution_session:
            raise ValueError(
                "execution_at must fall on the execution snapshot session "
                f"{expected_execution_session.isoformat()}"
            )
        if expected_decision_session >= expected_execution_session:
            raise ValueError(
                "the decision bar session must be strictly before the execution bar session"
            )

    @property
    def execution_session(self) -> date:
        """Session key used by idempotency and benchmarks."""
        return self.execution.as_of.date()
