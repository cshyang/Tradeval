# RetailTrader — Trading Philosophy Lab

A deterministic lab for testing versioned investment philosophies. Restricted
YAML specs produce auditable targets, orders, fills, portfolios, evaluations,
data provenance, and a static comparison UI.

This is fake-money research software, not financial advice.

## Shipped Scope

- Fixed 30-symbol US large-cap universe.
- Quality-value, GARP, and trend philosophy templates.
- Offline point-in-time synthetic bars and fundamentals.
- Optional adjusted daily prices through OpenBB with explicit Yahoo routing.
- Prior-close decisions, next-open fake fills, and next-close marks through one
  replay/paper transition.
- Enforced cash, concentration, no-leverage, and turnover constraints.
- Synthetic mega-cap, actual SPY, and equal-weight reference series, labeled by
  provenance rather than conflated.
- Static Next.js v3 frontend that displays engine-emitted values.

Streaming providers, scenarios, Pi narration, Flue, brokers, and real trading
are deliberately deferred. See `docs/demo-integrity.md`.

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

## Real Adjusted-Price Replay

The default demo remains network-free. To opt into historical adjusted daily
prices, install the OpenBB extra and run the trend-only replay:

```bash
uv sync --extra data-openbb
uv run --extra data-openbb retailtrader market-replay \
  --workspace runs/market \
  --cache data/cache \
  --start 2025-01-01 \
  --end 2025-06-30
```

The command starts with `$100,000` of fake cash, fetches the fixed universe plus
actual SPY through `OpenBB -> yfinance`, requests
`splits_and_dividends`-adjusted daily OHLCV, and requires 253 completed sessions
of trend warmup. Quality-value and GARP remain synthetic because real-data v1
does not ingest point-in-time fundamentals.

Every provider bar carries separate open and close availability timestamps.
Missing symbols, incomplete bars, insufficient history, future observations,
and incompatible resumes fail closed. Adjusted OHLC fills are normalized
research prices, not executable quotes. SPY and equal weight are no-cost,
fractional reference indices.

The fixed present-day universe introduces survivorship bias. Real-data runs are
therefore labeled **HINDSIGHT · ADJUSTED MARKET DATA** and classified as
`hindsight_current_universe`, never as a live track record. Synthetic runs
remain labeled **SYNTHETIC DEMO DATA**, and their five-stock reference is always
called **Synthetic mega-cap proxy**—never SPY.

Normalized data is stored in an immutable, content-checked Parquet cache under
`data/cache/<transport>/<provider>/<query-hash>/`. Each run also writes an
immutable `data-provenance.json` containing source, provider versions,
retrieval time, query and data hashes, adjustment, execution model, reference
method, and warnings.

The optional provider smoke test is explicitly opt-in:

```bash
RETAILTRADER_LIVE_DATA=1 uv run --extra data-openbb \
  pytest -m integration tests/integration/data/test_openbb_live.py -v
```

Yahoo Finance is an unofficial upstream with no availability SLA. Review
OpenBB and provider licensing before redistributing software or cached data.

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

`runs/`, `data/cache/`, `frontend/public/runs/`, and `frontend/out/` are generated and ignored.
Tracked files under `tests/fixtures/demo-run/` are frozen contract fixtures.

## Documentation

- `docs/architecture.md` — component and artifact boundaries.
- `docs/data-integrity.md` — timing, constraints, and limitations.
- `docs/experiment-lifecycle.md` — CLI and safe regeneration.
- `docs/demo-integrity.md` — current handoff and deferred roadmap.
- `docs/plans/2026-07-16-demo-integrity-closure-design.md` — closure decisions.
- `docs/plans/2026-07-16-live-market-data.md` — real-data design and plan.
- `docs/plans/2026-07-16-agenttrader-lab-design.md` — companion-project boundary.

UI source: Claude Design project `d153c1de-5e87-4a22-8ecf-da0c1ba944c4`,
`Philosophy Lab v3.dc.html`, implemented in `frontend/app/page.tsx`.
