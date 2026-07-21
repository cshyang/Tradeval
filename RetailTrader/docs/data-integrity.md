# Data Integrity

## Synthetic Boundary

The demo uses deterministic `synthetic-v1` data only. A symbol seed produces the
same daily bars and fundamentals on every run. The fixed universe introduces
survivorship and selection bias, so results demonstrate the machinery rather
than an investment edge.

Fundamental availability is approximated as 45 days after quarter end. This is
not a substitute for filing acceptance timestamps from a real point-in-time
provider.

Agent evidence uses SEC company facts conservatively available at 00:00 UTC on the day after
the filing date. Cached source payloads are immutable. Candidate and proposal hashes bind every
AI decision to the exact evidence set visible at its decision cutoff.

## Timing

For an execution session:

1. Signals use bars and fundamentals available by the prior completed close.
2. Orders fill at the incoming session open plus five basis points of slippage.
3. The portfolio is marked at the incoming session close.

The incoming close and same-session fundamental observations cannot influence
the open fill.

## Turnover

One-way turnover is:

```text
gross slippage-adjusted requested fill notional / (2 * opening equity)
```

When a philosophy cap would be exceeded, requested integer-share deltas are
scaled uniformly and floored in deterministic symbol order. Omitted shares are
recorded as rejected orders with reason `max turnover`. Sells still execute
before buys, and affordability checks prevent negative cash.

## Benchmarks

The first benchmark is an equal-weight synthetic basket of AAPL, MSFT, NVDA,
AMZN, and GOOGL. It is named **Synthetic mega-cap proxy** and is not SPY. The
second benchmark is an equal-weight proxy for the full fixed universe.

## Known Limitations

- Weekdays stand in for an exchange holiday calendar.
- Corporate actions and dividends are not modeled.
- Execution uses deterministic open prices and fixed slippage, not liquidity.
- Constituents are fixed across history.
- Fundamental timestamps are approximated and values are synthetic.
- Model knowledge is not historically time-gated, so every model-assisted replay is labeled
  `HINDSIGHT SCENARIO` even when market and filing inputs are point-in-time safe.
- Shorting, leverage, tax, borrow, sentiment, and transaction-level impact are absent.
