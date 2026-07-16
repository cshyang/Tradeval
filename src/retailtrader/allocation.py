"""Equal-weight allocation with risk constraints.

Version 0 uses equal weights after ranking — no numerical optimization.
Constraints applied here: cash buffer, maximum position weight (excess
moves to cash). ``max_turnover`` is declared on the spec but enforced at
the execution layer, which owns the prior portfolio state.
"""

from __future__ import annotations

from datetime import datetime

from retailtrader.domain import PhilosophySpec, TargetPortfolio, TargetPosition


def allocate(
    spec: PhilosophySpec,
    selected: list[str],
    run_id: str,
    as_of: datetime,
) -> TargetPortfolio:
    """Equal-weight the selected symbols within the spec's risk constraints."""
    if not selected:
        return TargetPortfolio(run_id=run_id, as_of=as_of, cash_weight=1.0, positions=())

    equal_weight = (1.0 - spec.cash_buffer) / len(selected)
    weight = min(equal_weight, spec.max_position_weight)
    cash_weight = 1.0 - weight * len(selected)
    positions = tuple(
        TargetPosition(symbol=symbol, weight=weight) for symbol in sorted(selected)
    )
    return TargetPortfolio(
        run_id=run_id, as_of=as_of, cash_weight=cash_weight, positions=positions
    )
