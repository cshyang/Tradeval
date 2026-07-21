# Tradeval

Tradeval is an auditable AI trading-philosophy laboratory built as two services:

- `AgentTrader/` interprets philosophies and orchestrates AI experiment proposals.
- `RetailTrader/` owns market data, evidence, screening, risk, simulation, ledger, and evaluation.

AI output is untrusted intent. RetailTrader deterministically constrains every proposal and
uses fake capital only; this repository contains no broker or real-order path.

See each service README and `RetailTrader/docs/plans/2026-07-16-agenttrader-first-slice.md`
for the current architecture and implementation plan.
