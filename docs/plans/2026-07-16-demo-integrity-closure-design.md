# Demo Integrity Closure Design

## Goal

Make every claim in the synthetic Philosophy Lab demo true from validated YAML through
execution artifacts, CLI output, and the static frontend. Live providers, Pi reporting,
scenarios, and brokers remain explicitly deferred.

## Decisions

### Simulation

- Enforce `max_turnover` in the execution layer, where current holdings, integer target
  quantities, open prices, and slippage are available.
- Define one-way turnover as gross fill notional divided by twice opening equity, matching
  the existing evaluation convention. Scale requested share deltas down deterministically
  when the cap would be exceeded and emit rejected-order records for omitted quantities.
- Persist fixed execution settings and synthetic source identities in the manifest. Resume
  only when the persisted manifest, philosophy YAML, event envelopes, initial cash, and
  execution settings match the requested run.
- Treat completed sessions as a chronological prefix. A paper step may process only the
  next scheduled session; replay processes only the remaining suffix.
- Signals for an execution session may use only observations available before that
  session's open. The portfolio fills at that open and marks at that close.

### Artifacts And Benchmarks

- Every persisted evaluation includes `schema_version` and `engine_version`.
- Identify sources as `synthetic-v1` and `synthetic-mega-cap-proxy-v1` in manifests and
  reports. Never label the five-stock proxy as SPY.
- The engine/export layer emits per-rebalance benchmark-relative values. The frontend may
  perform presentation geometry and formatting, but no financial calculations.
- Export validates all runs before writing, builds a complete temporary tree, and swaps it
  into place so failure cannot leave a partial or stale frontend dataset.

### CLI

Provide an explicitly synthetic lifecycle:

```text
philosophy validate
experiment create
experiment replay
experiment evaluate
experiment compare
paper step
demo
export
```

Commands support stable text and JSON output, meaningful exit classes, explicit workspace
and run selection where applicable, and idempotent reruns. There is no `data fetch` command
until a real provider/cache boundary exists.

### Frontend And Operations

- Preserve the v3 desktop design while adding honest loading/error/empty states, keyboard
  controls, and deterministic browser coverage at the declared supported viewport.
- Upgrade Next.js within the existing major line to a patched release and verify the lockfile
  with `npm audit` and a production build.
- Track architecture, data-integrity, lifecycle, and demo-status documentation. `.remember`
  remains disposable local memory.
- Remove the two already-merged agent worktrees only after the closure branch is verified.

## Acceptance

The closure is accepted when backend tests and Ruff pass; lifecycle CLI and export tests
cover success, idempotency, and corruption paths; the demo regenerates three constrained
runs; the frontend build and browser smoke pass; dependency audit has no high/critical
finding; generated artifacts do not dirty Git; and the main worktree contains the closure
changes without re-merging historical agent branches.
