# RetailTrader — Agent Rules

Non-negotiable:

- No real-money trading.
- No broker integration in v0.
- No LLM-generated order decisions.
- No historical observation without an availability timestamp.
- No separate backtest and paper-trading calculation paths.
- The deterministic engine (`src/retailtrader/`) is the only component that
  calculates scores, weights, orders, fills, cash, or positions. The frontend
  and any LLM only display or narrate engine artifacts.

Contract freeze: `src/retailtrader/domain.py` is the shared contract. During
the parallel build (Phase 1), do not modify it from a worktree — surface the
need instead.

Plan of record: `docs/plans/2026-07-16-trading-philosophy-lab.md`
(see "3-Hour Demo Build Mode" for current scope cuts).
