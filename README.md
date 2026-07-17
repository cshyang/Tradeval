# RetailTrader — Trading Philosophy Lab

A deterministic lab for testing investment philosophies as versioned,
falsifiable specifications. The engine replays each philosophy into an
isolated fake-money portfolio and emits auditable decisions, simulated orders,
fills, positions, evaluation, and data-provenance artifacts.

Research prototype. No broker integration, real orders, or financial advice.

- Architecture: `docs/plans/2026-07-16-agenttrader-lab-design.md`
- Implementation plan: `docs/plans/2026-07-16-live-market-data.md`
- Original plan: `docs/plans/2026-07-16-trading-philosophy-lab.md`
- Rules: `AGENTS.md`

## Offline quickstart

The default installation and test suite remain network-free. The demo uses
seeded synthetic prices and fundamentals.

```bash
uv sync
uv run pytest -q
uv run retailtrader demo --workspace runs/demo
uv run retailtrader export --workspace runs/demo
cd frontend && npm ci && npm run build && npx serve out
```

The UI reads engine artifacts and labels these runs **SYNTHETIC DEMO DATA**.

## Real adjusted-price replay

The optional market-data extra installs OpenBB with its Yahoo Finance provider:

```bash
uv sync --extra data-openbb
```

Run the deterministic trend philosophy over completed adjusted daily bars:

```bash
uv run --extra data-openbb retailtrader market-replay \
  --workspace runs/market \
  --cache data/cache \
  --start 2025-01-01 \
  --end 2025-06-30
```

This command:

- Starts with **$100,000 of fake cash**.
- Fetches the fixed 30-stock universe plus actual SPY through explicit
  `OpenBB -> yfinance` routing.
- Requests `1d` bars with `splits_and_dividends` adjustment.
- Fetches 400 calendar days of warmup and requires at least 253 completed
  sessions before the first trend decision.
- Calculates a decision after session `T` closes, simulates constrained fills
  at the next session `T+1` open plus slippage, and marks at `T+1` close.
- Uses the same transition, ledger, and artifact path as the synthetic replay.
- Runs only `trend-v1`; quality-value and GARP remain synthetic because v1 does
  not ingest point-in-time fundamentals.

Export this run separately if desired:

```bash
uv run retailtrader export \
  --workspace runs/market \
  --out frontend/public/runs
cd frontend && npm ci && npm run build
```

The UI labels it **HINDSIGHT · ADJUSTED MARKET DATA**. It is historical
fake-money research—not streaming data or forward paper trading.

## Data integrity and interpretation

### Point-in-time timing

Every provider bar carries separate modeled open and close availability
timestamps before conversion to the frozen engine contract. Regular US market
hours are modeled as 09:30 and 16:00 `America/New_York`, including daylight
saving time. The 16:00 convention is conservative on early-close sessions;
early-close timestamps are not modeled in this version.

The execution bar is never exposed to scoring. Missing bars, insufficient
warmup, incomplete symbols, future-dated observations, and unfinished daily
bars fail closed rather than being forward-filled or fabricated.

### Adjusted prices and references

Strategy and reference series use the same `splits_and_dividends` adjustment.
Dividends are therefore not added separately. Simulated fills use normalized
adjusted OHLC values and are research approximations, not observed executable
quotes.

SPY and equal-weight results are no-cost fractional buy-and-hold references.
They are notionally funded at the first execution open and marked at subsequent
execution closes. They do not include strategy slippage or periodic
rebalancing.

The universe is today’s fixed large-cap list, so historical results carry
survivorship bias and are classified as `hindsight_current_universe`, not as a
live track record.

### Immutable cache and provenance

Normalized results are cached under:

```text
data/cache/<transport>/<provider>/<query-hash>/data.parquet
data/cache/<transport>/<provider>/<query-hash>/metadata.json
```

Entries are complete-directory atomic, immutable, content-checked, and keyed
by every source/query parameter. Metadata records package versions, retrieval
time, adjustment, raw and normalized hashes, symbol completeness, source
references, and session bounds. A conflicting response for an existing query
is rejected instead of overwriting research history. Extending `--end` creates
a new query key.

Each run writes `data-provenance.json`. Run identity includes dates, source,
adjustment, data hash, philosophy and universe hashes, engine version, initial
cash, slippage, execution model, and reference method. Incompatible resumes
fail before ledger restoration.

## External-provider smoke test

Default tests never access the network. Explicitly opt in with:

```bash
RETAILTRADER_LIVE_DATA=1 uv run --extra data-openbb \
  pytest -m integration tests/integration/data/test_openbb_live.py -v
```

Yahoo Finance is a keyless, unofficial upstream with no SLA; throttling,
revisions, or schema changes remain possible. OpenBB packages are distributed
under AGPL terms, while Yahoo data is governed by separate provider terms.
Review both sets of obligations before distributing software or cached data.

## Generated paths

`runs/`, `data/cache/`, `frontend/public/runs/`, and `frontend/out/` are
generated and ignored. Regenerate them after engine or frontend changes.

UI design source: claude.ai/design project "Trading Philosophy Lab Demo"
(`Philosophy Lab v2.dc.html`), ported to React in `frontend/app/page.tsx`.
