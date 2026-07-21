# Architecture

## Boundaries

```text
philosophy YAML
  -> validated PhilosophySpec
  -> point-in-time factor scoring
  -> TargetPortfolio
  -> shared execution step
  -> append-only events + materialized artifacts
  -> evaluation/export
  -> static frontend
```

The agent path is parallel to deterministic philosophy scoring:

```text
natural language -> Pi AI-INTERPRETED spec -> frozen mandate
-> RetailTrader candidate set -> Pi desired weights
-> RetailTrader adjudication -> shared execution step -> evaluation
```

`src/retailtrader/` owns every financial calculation. The frontend consumes one
engine-emitted `runs/data.json` view model. It formats values, selects rows, and
maps series into SVG coordinates; it does not derive financial results.

## Shared Transition

`simulation.runner.step` is the sole portfolio transition. Historical replay
loops over scheduled snapshots. `paper step` invokes the same function once for
the next scheduled session. The event log seals each session with
`rebalance_completed`; resume requires completed sessions to form a prefix.

## Artifact Trust Chain

Each run persists:

```text
manifest.json       immutable identity, sources, execution settings
philosophy.yaml      exact validated input
events.jsonl         append-only transition authority
decisions.jsonl      materialized target decisions
orders.jsonl         created and rejected orders
fills.jsonl          completed fills
portfolio.jsonl      close-marked portfolios
equity.csv           portfolio and benchmark series
evaluation.json      versioned metrics and fidelity
report.md            human-readable evaluation
```

On resume, identity and materialized files are reconciled against the event log
before any append. Export validates every run, writes a complete temporary tree,
then replaces the old frontend dataset.

## Replaceable Edges

Synthetic data, a future live provider, Pi narration, Flue supervision, and a
broker mirror are adapters around stable philosophy, target, fill, portfolio,
manifest, and evaluation contracts. None may become the source of portfolio truth.

AgentTrader owns API jobs, Pi sessions, scheduling, and progress. RetailTrader owns market
data, point-in-time evidence, candidate screening, every constraint, simulation, accounting,
and evaluation. There is no broker or real-order adapter.
