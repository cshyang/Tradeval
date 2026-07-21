# Experiment Lifecycle

Agent experiments freeze a mandate and model protocol before work begins. Hindsight preparation
creates deterministic frame sources; screening seals the candidate hash; Pi emits intent; and
RetailTrader seals a prepared frame before deterministic adjudication. Repeating an identical
session is a no-op, while conflicting content is an integrity error.

Forward-paper schedules use the same candidate, proposal, and step contracts. Only the clock and
temporal classification differ. Missing evidence or execution bars produce abstention or deferral,
never relaxed limits.

## Commands

Create an immutable synthetic run:

```bash
uv run retailtrader experiment create philosophies/trend-v1.yaml \
  --workspace runs/lab --run-id trend-demo \
  --start 2024-01-05 --end 2026-06-26
```

Advance exactly the next scheduled session or replay the remaining suffix:

```bash
uv run retailtrader paper step \
  --workspace runs/lab --run-id trend-demo --session 2024-01-05
uv run retailtrader experiment replay \
  --workspace runs/lab --run-id trend-demo
```

Repeating a completed paper session is a successful no-op. Skipping ahead or
changing immutable inputs is a conflict. Replay resumes only after a valid
chronological prefix.

Evaluate and compare:

```bash
uv run retailtrader experiment evaluate \
  --workspace runs/lab --run-id trend-demo
uv run retailtrader experiment compare \
  --workspace runs/lab \
  --run-id quality-demo --run-id garp-demo --run-id trend-demo
```

All lifecycle commands accept `--format json`. Successful no-ops exit zero.
Invalid inputs use exit 3, missing artifacts exit 4, and immutable/order conflicts
exit 5. Typer syntax errors use exit 2.

## Demo And Export

```bash
uv run retailtrader demo --workspace runs/demo
uv run retailtrader export \
  --workspace runs/demo --out frontend/public/runs
```

Export requires evaluated comparable runs and rejects destinations inside the
source workspace. It is atomic: validation or serialization failure leaves an
existing destination unchanged, and successful replacement removes stale runs.

## Cleanup

These directories are generated and safe to remove when no process is using them:

```text
runs/
frontend/public/runs/
frontend/.next/
frontend/out/
```

Do not remove `tests/fixtures/demo-run/`; those files freeze artifact contracts.
Do not use `git clean -fdX`, which also removes local environments and session state.
