# Tradeval

Tradeval is an auditable AI trading-philosophy laboratory built as two services:

- `AgentTrader/` interprets philosophies and orchestrates AI experiment proposals.
- `RetailTrader/` owns market data, evidence, screening, risk, simulation, ledger, and evaluation.

AI output is untrusted intent. RetailTrader deterministically constrains every proposal and
uses fake capital only; this repository contains no broker or real-order path.

The shipped vertical slice supports AI-interpreted philosophy drafts, frozen mandates,
evidence-bound proposals, deterministic adjudication, hindsight scenarios, forward-paper
scheduling, reconnectable progress events, and an API-aware experiment builder.

Historical output is always `HINDSIGHT SCENARIO`; philosophy translation is always
`AI-INTERPRETED`. Neither label represents a track record or an authentic investor strategy.

See `AgentTrader/README.md`, `RetailTrader/docs/architecture.md`, and
`AgentTrader/docs/artifacts.md` for runtime and audit details.
