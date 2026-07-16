# RetailTrader — Trading Philosophy Lab

A deterministic lab for testing versioned investment philosophies. Restricted
YAML specs produce auditable targets, orders, fills, portfolios, evaluations,
and a static comparison UI.

This is a synthetic research prototype, not financial advice.

## Shipped Scope

- Fixed 30-symbol US large-cap universe.
- Quality-value, GARP, and trend philosophy templates.
- Point-in-time synthetic bars and fundamentals.
- Historical replay and explicit-session paper stepping through one transition.
- Enforced cash, concentration, no-leverage, and turnover constraints.
- Synthetic mega-cap and equal-weight benchmark proxies.
- Static Next.js v3 frontend that displays engine-emitted values.

Live providers, scenarios, Pi narration, Flue, brokers, and real trading are
deliberately deferred. See `docs/demo-integrity.md`.

## Setup

```bash
uv sync --frozen
npm --prefix frontend ci
```

## Run The Demo

```bash
uv run retailtrader demo --workspace runs/demo
uv run retailtrader export --workspace runs/demo --out frontend/public/runs
npm --prefix frontend run build
npx --prefix frontend serve frontend/out
```

The engine creates all financial values. The frontend may format values and
draw chart geometry, but does not calculate returns, scores, weights, orders,
cash, or positions.

## Experiment Lifecycle

```bash
uv run retailtrader philosophy validate philosophies/trend-v1.yaml
uv run retailtrader experiment create philosophies/trend-v1.yaml \
  --workspace runs/lab --run-id trend-demo \
  --start 2024-01-05 --end 2026-06-26
uv run retailtrader paper step \
  --workspace runs/lab --run-id trend-demo --session 2024-01-05
uv run retailtrader experiment replay --workspace runs/lab --run-id trend-demo
uv run retailtrader experiment evaluate --workspace runs/lab --run-id trend-demo
```

Add `--format json` to any lifecycle command for stable machine output. Full
command behavior is documented in `docs/experiment-lifecycle.md`.

## Acceptance

```bash
uv run ruff check .
uv run pytest -q
uv run retailtrader demo --workspace runs/acceptance
uv run retailtrader export \
  --workspace runs/acceptance --out frontend/public/runs
npm --prefix frontend audit --omit=dev --audit-level=high
npm --prefix frontend run build
git status --short
```

`runs/`, `frontend/public/runs/`, and `frontend/out/` are generated and ignored.
Tracked files under `tests/fixtures/demo-run/` are frozen contract fixtures.

## Documentation

- `docs/architecture.md` — component and artifact boundaries.
- `docs/data-integrity.md` — timing, constraints, and limitations.
- `docs/experiment-lifecycle.md` — CLI and safe regeneration.
- `docs/demo-integrity.md` — current handoff and deferred roadmap.
- `docs/plans/2026-07-16-demo-integrity-closure-design.md` — closure decisions.

UI source: Claude Design project `d153c1de-5e87-4a22-8ecf-da0c1ba944c4`,
`Philosophy Lab v3.dc.html`, implemented in `frontend/app/page.tsx`.
