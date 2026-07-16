# Live Market Data Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an explicit, fake-money trend replay over real adjusted daily OpenBB/Yahoo prices while preserving the synthetic offline default, point-in-time integrity, and the frozen domain contract.

**Architecture:** First correct the shared simulator so a decision at session `T` close can never observe the execution bar used at `T+1` open. Then add provider-neutral availability-bearing price contracts, an immutable Parquet cache, and a lazy optional OpenBB/Yahoo source. Build both synthetic and real-price runs as the same sequence of decision/execution frames, emit per-run provenance, and render that provenance instead of a hard-coded synthetic label.

**Tech Stack:** Python 3.12, Pydantic domain models, dataclasses/protocols, pandas/PyArrow, Typer, OpenBB 4.7.2 with openbb-yfinance 1.6.3 as an optional extra, pytest, Ruff, Next.js 15, React 19, TypeScript.

---

## Constraints and Decisions

- Do not modify `src/retailtrader/domain.py`.
- No broker, real order, real money, or LLM decision path.
- `retailtrader demo` remains synthetic and network-free by default.
- The first real-data strategy is `philosophies/trend-v1.yaml`; fundamentals-dependent recipes remain synthetic.
- Use `provider="yfinance"`, `interval="1d"`, `adjustment="splits_and_dividends"`, and `extended_hours=False` explicitly. Do not rely on OpenBB provider defaults.
- Every ingested bar carries field-level `open_available_at` and `close_available_at` timestamps before it may become a frozen-domain `MarketBar` in a snapshot.
- A daily open is modeled as available at 09:30 and the completed bar at 16:00 `America/New_York`, converted to UTC. The close convention is conservative on early-close days.
- The RetailTrader command in this plan is historical replay only. It does not claim to provide forward-paper updates from daily bars fetched after close.
- Real replay uses today’s fixed 30-stock universe and is therefore labeled a **current-universe hindsight scenario**, even when every price is time-gated.
- SPY and equal-weight series remain no-cost references in v1. Both are notionally bought at the first execution open with fractional shares, then marked at execution closes without rebalancing or costs; provenance must say they are references, not simulated control portfolios.
- OpenBB documentation checked on 2026-07-16:
  - `https://docs.openbb.co/odp/python/reference/equity/price/historical`
  - `https://docs.openbb.co/odp/python/data_models/EquityHistorical`
  - `https://docs.openbb.co/odp/python/quickstart`

## Task 1: Enforce Prior-Close Decision and Next-Open Execution

**Files:**
- Create: `src/retailtrader/simulation/frame.py`
- Modify: `src/retailtrader/simulation/runner.py`
- Modify: `src/retailtrader/simulation/execution.py`
- Create: `src/retailtrader/storage/transitions.py`
- Modify: `src/retailtrader/storage/events.py`
- Modify: `src/retailtrader/storage/artifacts.py`
- Modify: `src/retailtrader/cli.py`
- Modify: `tests/helpers.py`
- Create: `tests/unit/simulation/test_frame.py`
- Modify: `tests/unit/simulation/test_execution.py`
- Create: `tests/unit/storage/test_transitions.py`
- Modify: `tests/integration/test_replay_parity.py`

**Step 1: Write the failing frame-ordering tests**

Create tests that construct separate decision and execution snapshots and assert:

```python
frame = SimulationFrame(
    decision=make_snapshot(date(2024, 1, 4), decision_prices),
    execution=make_snapshot(date(2024, 1, 5), execution_prices),
    execution_at=datetime(2024, 1, 5, 14, 30, tzinfo=UTC),
)
assert frame.decision.as_of < frame.execution_at < frame.execution.as_of
```

Also test rejection when the decision snapshot is not earlier, the execution timestamp is naive, or a decision bar session is not strictly before an execution bar session.

Add an integration test whose execution bar contains an extreme price and whose target generator records every bar it receives. Assert the generator sees only the decision snapshot.

**Step 2: Run the focused tests to verify failure**

Run:

```bash
uv run pytest tests/unit/simulation/test_frame.py tests/integration/test_replay_parity.py -v
```

Expected: FAIL because `SimulationFrame` and the two-snapshot runner API do not exist.

**Step 3: Implement the orchestration contract**

In `simulation/frame.py`, add a frozen dataclass:

```python
@dataclass(frozen=True)
class SimulationFrame:
    decision: MarketSnapshot
    execution: MarketSnapshot
    execution_at: datetime
```

Validate UTC awareness and `decision.as_of < execution_at < execution.as_of`. Validate every decision bar session is strictly earlier than every execution bar session.

Change module-level `step`, `ExperimentRunner.step`, and `ExperimentRunner.replay` to consume `SimulationFrame`. Keep this as the one transition used by replay and restarted forward stepping.

Inside the transition:

- Call the target generator with `frame.decision` only.
- Execute and mark with `frame.execution` only.
- Key idempotency and benchmarks by the execution-close timestamp/session.
- Append `target_generated` at decision close.
- Append `order_created`, cash-affordability rejection, and fill events at execution open because quantities depend on execution-open prices and opening equity.
- Append portfolio mark and completion at execution close.

Make `filled_at` a required keyword argument to `execute_rebalance`. Set:

- `OrderIntent.as_of = execution_at`
- `FillEvent.filled_at = execution_at`
- `PortfolioSnapshot.as_of = execution_snapshot.as_of`

Reject a target whose `as_of` differs from the decision snapshot. Add a test proving that changing only the execution open changes order quantity and that no event claims the quantity existed at decision close.

**Step 4: Make each transition atomic and restart-safe**

Treat one immutable journal file as the source of truth for each completed transition:

```text
<run-dir>/transitions/<execution-session>.json
```

The runner computes target, decisions, orders, rejections, fills, marked portfolio, references, and all event timestamps in memory. `TransitionStore.commit(...)` flushes and fsyncs one temporary file, atomically renames it to the final session path, then fsyncs the containing `transitions/` directory before reporting success or beginning materialization. A conflicting existing session raises an integrity error.

`EventLog` and `RunWriter` materialize `events.jsonl`, decisions/orders/fills/portfolio JSONL, and equity CSV deterministically from the initial run metadata plus sorted committed transitions. Materialization writes temporary complete files and replaces each derived artifact. On startup, rematerialize before restoring the ledger. A crash before journal rename commits nothing; a crash after journal rename is recovered by rematerialization without duplicate fills.

Add failure-injection tests immediately before/after journal rename, before/after parent-directory fsync, and after every derived-artifact replacement. In the rename/fsync window, restart may observe either atomic outcome and must materialize only journals actually present. Completed restart must produce byte-identical artifacts and one fill set per execution session.

**Step 5: Update helpers, parity tests, and the synthetic CLI**

Add `make_frame(decision_session, decision_prices, execution_session, execution_prices)` to `tests/helpers.py`. Update direct execution tests with explicit fill timestamps. Update replay/forward parity so both modes consume the same ordered frame sequence, with a fresh runner per frame in forward mode.

Migrate `retailtrader demo` in this task so it builds synthetic decisions at the existing weekly decision sessions and execution snapshots at the next synthetic trading session. This keeps the CLI usable in the same commit. Smoke-test the command and assert it makes no network calls.

**Step 6: Run simulation verification**

Run:

```bash
uv run pytest tests/unit/simulation tests/unit/storage tests/integration/test_replay_parity.py -v
uv run pytest -q
uv run retailtrader demo --workspace /tmp/retailtrader-task1
uv run ruff check src/retailtrader/simulation src/retailtrader/storage src/retailtrader/cli.py tests
```

Expected: PASS. Assertions prove `decision close < order/fill at next open < mark at next close`, no execution bar reaches scoring, interrupted transitions recover without duplication, idempotency remains intact, ledger reconstruction matches, and the synthetic CLI remains usable.

**Step 7: Commit**

```bash
git add src/retailtrader/simulation src/retailtrader/storage src/retailtrader/cli.py tests/helpers.py tests/unit/simulation tests/unit/storage tests/integration/test_replay_parity.py
git commit -m "fix: enforce atomic prior-close next-open transitions"
```

## Task 2: Define Availability-Bearing Price Contracts

**Files:**
- Create: `src/retailtrader/data/protocol.py`
- Modify: `src/retailtrader/data/__init__.py`
- Create: `tests/unit/data/test_protocol.py`

**Step 1: Write failing query and batch tests**

Cover:

- Uppercase, sorted, unique symbols.
- Rejection of empty symbols and `start > end`.
- UTC-aware `open_available_at`, `close_available_at`, and `retrieved_at` with `open_available_at < close_available_at <= retrieved_at`.
- Rejection of duplicate `(symbol, session)` observations.
- Rejection of observations outside the query, canonical availability earlier than the bar session, or availability after retrieval.
- Stable canonical JSON, normalized-row hash, and SHA-256 cache keys.
- Key changes when transport, provider, symbol, dates, interval, or adjustment changes.

**Step 2: Verify failure**

Run:

```bash
uv run pytest tests/unit/data/test_protocol.py -v
```

Expected: FAIL because the contracts do not exist.

**Step 3: Implement provider-neutral contracts**

Add:

```python
@dataclass(frozen=True)
class PriceQuery:
    symbols: tuple[str, ...]
    start: date
    end: date
    interval: Literal["1d"] = "1d"
    adjustment: Literal["splits_and_dividends"] = "splits_and_dividends"

@dataclass(frozen=True)
class AvailableMarketBar:
    bar: MarketBar
    open_available_at: datetime
    close_available_at: datetime
    source_ref: str

@dataclass(frozen=True)
class PriceBatch:
    transport: str
    provider: str
    query: PriceQuery
    observations: tuple[AvailableMarketBar, ...]
    retrieved_at: datetime
    raw_hash: str
    normalized_hash: str
    provider_versions: tuple[tuple[str, str], ...]

class DailyPriceSource(Protocol):
    transport: str
    provider: str
    def fetch(self, query: PriceQuery) -> PriceBatch: ...
```

Provide canonical serialization and `query_key(transport, provider, query)`. Sort observations by `(session, symbol)` during validation and compute `normalized_hash` from those canonical rows. Require an immutable sorted tuple for provider versions. A source or cache wrapper must reject a returned batch whose transport/provider identity differs from the source identity. Keep all availability metadata outside `domain.py`.

**Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/unit/data/test_protocol.py -v
uv run pytest -q
uv run ruff check src/retailtrader/data tests/unit/data/test_protocol.py
```

Then:

```bash
git add src/retailtrader/data tests/unit/data/test_protocol.py
git commit -m "feat: define point-in-time daily price contracts"
```

## Task 3: Add the Immutable Parquet Cache

**Files:**
- Create: `src/retailtrader/data/cache.py`
- Create: `tests/unit/data/test_cache.py`

**Step 1: Write failing cache tests**

Test cache miss, exact round trip, provider/query isolation, a cache hit that does not call the fake source, atomic failure cleanup, immutable conflicting writes, metadata mismatch, and Parquet corruption detection.

**Step 2: Verify failure**

Run:

```bash
uv run pytest tests/unit/data/test_cache.py -v
```

Expected: FAIL because `PriceCache` does not exist.

**Step 3: Implement cache storage**

Implement:

```python
class PriceCache:
    def load(self, transport: str, provider: str, query: PriceQuery) -> PriceBatch | None: ...
    def store(self, batch: PriceBatch) -> None: ...

@dataclass(frozen=True)
class PriceFetchResult:
    batch: PriceBatch
    cache_status: Literal["hit", "miss", "bypass"]

class DailyPriceLoader(Protocol):
    def fetch(self, query: PriceQuery) -> PriceFetchResult: ...

class CachedDailyPriceSource:
    def fetch(self, query: PriceQuery) -> PriceFetchResult: ...
```

`CachedDailyPriceSource` validates that the batch returned by its raw source matches the configured transport/provider and reports `hit` or `miss`. Fixture-backed loaders may report `bypass`. CLI internals accept `DailyPriceLoader`; raw adapters remain `DailyPriceSource`.

Store:

```text
data/cache/<transport>/<provider>/<query-hash>/data.parquet
data/cache/<transport>/<provider>/<query-hash>/metadata.json
```

Parquet columns are `symbol`, `session`, decimal OHLC strings, integer `volume`, `open_available_at`, `close_available_at`, and `source_ref`. Metadata records transport, provider, provider package versions, canonical query, query hash, retrieval timestamp, adjustment, source references, raw hash, normalized-row hash, symbol completeness, and session range.

Build each entry in a temporary sibling directory, fsync its files and directory, then atomically rename the complete directory to the final query-hash path. Never overwrite an existing query key with different bytes. On load, validate transport/provider/query metadata and recompute the normalized-row hash; corruption raises a dedicated integrity error rather than silently refetching. Add concurrent-writer and orphan-temporary-directory tests.

**Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/unit/data/test_protocol.py tests/unit/data/test_cache.py -v
uv run pytest -q
uv run ruff check src/retailtrader/data tests/unit/data
```

Then:

```bash
git add src/retailtrader/data/cache.py tests/unit/data/test_cache.py
git commit -m "feat: add immutable market data cache"
```

## Task 4: Implement the Optional OpenBB/Yahoo Source

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/retailtrader/data/openbb.py`
- Create: `tests/fixtures/market_data/openbb_yfinance_aapl_daily.json`
- Create: `tests/unit/data/test_openbb.py`
- Create: `tests/integration/data/__init__.py`
- Create: `tests/integration/data/test_openbb_live.py`

**Step 1: Add the optional dependency declaration and marker**

Add:

```toml
[project.optional-dependencies]
data-openbb = [
    "openbb>=4.7.2,<5",
    "openbb-yfinance>=1.6.3,<2",
]
```

Append this line to the existing `[tool.pytest.ini_options]` table rather than creating a duplicate table:

```toml
markers = ["integration: opt-in tests that may access external services"]
```

Run `uv lock`, but do not install the extra into the default test environment yet.

**Step 2: Write fixture-driven failing tests**

Use an injectable historical callable and frozen raw-result fixtures covering both one-symbol and two-symbol queries. Assert exact arguments:

```python
historical(
    symbol="AAPL",
    provider="yfinance",
    interval="1d",
    adjustment="splits_and_dividends",
    extended_hours=False,
    start_date=query.start,
    end_date=query.end + timedelta(days=1),
)
```

Test inclusive filtering, stable raw and normalized hashes, exact source references, `Decimal(str(value))` conversion, 09:30 open and 16:00 close availability in New York, and rejection of incomplete periods, missing/non-finite OHLCV, provider mismatch, duplicate rows, invalid OHLC relationships, and incomplete symbols.

Test the missing-extra path by injecting or monkeypatching the importer so the assertion is independent of the developer environment. The error must contain `uv sync --extra data-openbb`.

**Step 3: Verify failure**

Run:

```bash
uv run pytest tests/unit/data/test_openbb.py -v
```

Expected: FAIL because the adapter does not exist.

**Step 4: Implement the lazy adapter**

Implement `OpenBBYFinancePriceSource` with separate `transport = "openbb"` and `provider = "yfinance"`. Import `from openbb import obb` only when no injected callable is supplied. Call each symbol separately to preserve identity across provider result shapes. Filter results back to the inclusive query interval because provider end-date behavior differs.

Hash canonical sorted raw provider rows before normalization and canonical normalized observations afterward. Record `openbb` and `openbb-yfinance` package versions as a sorted immutable tuple. Model each session’s open at 09:30 and close at 16:00 America/New_York. Reject an unfinished row or any row whose close availability is later than retrieval time; this prevents caching a current daily bar before completion.

**Step 5: Add the opt-in smoke test**

Guard the live test with both:

```python
@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RETAILTRADER_LIVE_DATA") != "1",
    reason="set RETAILTRADER_LIVE_DATA=1 to access OpenBB/Yahoo",
)
```

Fetch a small completed historical AAPL window and assert transport, provider, adjustment, UTC open/close availability, sorted sessions, and valid bars.

**Step 6: Verify offline and optionally live**

Run:

```bash
uv run pytest tests/unit/data/test_openbb.py -v
uv run pytest tests/unit/data -v
uv run pytest -q
uv run ruff check src/retailtrader/data tests/unit/data tests/integration/data
```

Optional network verification:

```bash
RETAILTRADER_LIVE_DATA=1 uv run --extra data-openbb \
  pytest -m integration tests/integration/data/test_openbb_live.py -v
```

**Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/retailtrader/data/openbb.py tests/fixtures/market_data tests/unit/data/test_openbb.py tests/integration/data
git commit -m "feat: add OpenBB Yahoo daily price source"
```

## Task 5: Build Weekly Point-in-Time Replay Frames and References

**Files:**
- Create: `src/retailtrader/data/replay.py`
- Create: `tests/unit/data/test_replay.py`

**Step 1: Write failing scheduling and leakage tests**

Build a fixture batch containing SPY and several strategy symbols across a weekend and a holiday-shaped gap. Test:

- The decision uses one completed session and execution uses the next actual SPY session.
- Every decision-history bar satisfies both `bar.session <= decision_session` and `close_available_at <= decision.as_of`.
- Every execution bar satisfies `open_available_at <= execution_at` and `close_available_at <= execution.as_of`.
- SPY is excluded from strategy snapshots and scoring history.
- A future-session bar with a deliberately backdated availability timestamp is rejected.
- A missing strategy-symbol decision/execution bar fails with named symbols.
- An extreme execution bar is absent from factor history.
- SPY and equal-weight references use exact expected values from the first execution open, including an overnight-gap fixture.

**Step 2: Verify failure**

Run:

```bash
uv run pytest tests/unit/data/test_replay.py -v
```

Expected: FAIL because the replay builder does not exist.

**Step 3: Implement time and session helpers**

Use `ZoneInfo("America/New_York")` to create 09:30 open and 16:00 close timestamps converted to UTC.

Implement weekly scheduling from actual SPY sessions, not a hand-written holiday calendar. Select the last completed SPY session in an ISO week as the decision session and the immediately following SPY session as execution. The evaluation `start/end` filter applies to execution sessions; fetch range includes at least seven days after the requested end so the final eligible decision can execute only when an actual next session exists. Detect implausibly long SPY gaps and fail with a provider-calendar diagnostic; document that SPY-derived sessions cannot distinguish an exchange holiday from a missing provider row.

**Step 4: Implement frames and point-in-time history**

Implement:

```python
def build_price_frames(
    batch: PriceBatch,
    universe: Sequence[str],
    start: date,
    end: date,
    benchmark_symbol: str = "SPY",
) -> tuple[SimulationFrame, ...]: ...

def history_as_of(
    batch: PriceBatch,
    universe: Sequence[str],
    decision_as_of: datetime,
) -> dict[str, tuple[MarketBar, ...]]: ...
```

Gate every wrapped bar before unwrapping it into `MarketSnapshot`. History admission requires both session ordering and close availability. Execution admission separately validates open availability at `execution_at` and close availability at the mark timestamp. Decision snapshots contain strategy symbols only; execution snapshots contain strategy symbols only. Preserve the SPY rows separately for references.

**Step 5: Implement no-cost reference indices**

Implement `build_reference_indices(...)` as two fractional-share, no-cost buy-and-hold references. At the first execution open, notionally buy SPY and an equal-dollar fixed basket of the strategy universe; do not rebalance. Mark both at every execution close. Use the same adjusted-price batch as the strategy and do not add dividends separately. Record `reference_method_version = "execution_open_fixed_basket_v1"` and name these `no_cost_reference` series, not simulated portfolios.

**Step 6: Verify and commit**

Run:

```bash
uv run pytest tests/unit/data/test_replay.py tests/unit/test_factors.py tests/unit/test_scoring_allocation.py -v
uv run pytest -q
uv run ruff check src/retailtrader/data/replay.py tests/unit/data/test_replay.py
```

Then:

```bash
git add src/retailtrader/data/replay.py tests/unit/data/test_replay.py
git commit -m "feat: build point-in-time price replay frames"
```

## Task 6: Add Run Provenance and the Real-Price Trend Replay CLI

**Files:**
- Modify: `src/retailtrader/storage/artifacts.py`
- Modify: `src/retailtrader/cli.py`
- Modify: `src/retailtrader/evaluation/report.py`
- Create: `tests/unit/test_cli.py`
- Modify: `tests/unit/evaluation/test_report.py`
- Create: `tests/integration/test_market_replay.py`

**Step 1: Write failing provenance and CLI tests**

Test that:

- Synthetic demo never constructs an OpenBB source.
- Every run writes `data-provenance.json`.
- An offline fixture-backed market replay runs only the trend philosophy.
- Run identity changes with dates, transport, provider, adjustment, query hash, normalized data hash, philosophy hash, universe hash, engine version, initial cash, slippage, execution-model version, and reference-method version.
- `created_at` does not affect identity.
- Reusing a run directory with mismatched identity or provenance fails before `RunWriter` creates files or ledger restoration begins.
- Market replay uses actual SPY reference data and fake cash.
- Decision, fill, and mark timestamps follow prior-close/next-open/next-close ordering.
- Fewer than three frames, incomplete lookback, missing bars, or a fundamentals recipe fails before partial artifacts are created.

**Step 2: Verify failure**

Run:

```bash
uv run pytest tests/unit/test_cli.py tests/integration/test_market_replay.py -v
```

Expected: FAIL because provenance and `market-replay` do not exist.

**Step 3: Add immutable per-run provenance**

Add `RunWriter.write_data_provenance(payload)` for `data-provenance.json`. Synthetic provenance includes:

```json
{
  "kind": "synthetic",
  "validity": "synthetic_demo",
  "label": "SYNTHETIC DEMO DATA",
  "provider": "synthetic",
  "adjustment": "none"
}
```

Real provenance includes:

- `kind = "real_market"`
- `validity = "hindsight_current_universe"`
- label `HINDSIGHT · ADJUSTED MARKET DATA`
- OpenBB transport and yfinance provider versions
- adjustment policy
- retrieval timestamp
- query and normalized-data hashes
- source references
- fixed-universe survivorship warning
- warning that adjusted OHLC fills are normalized research prices, not observed executable quotes
- `benchmark_kind = "no_cost_reference"`
- `reference_method_version = "execution_open_fixed_basket_v1"`

Define one canonical run-identity document containing evaluation dates, transport/provider, adjustment, query and normalized hashes, philosophy/universe/engine hashes, initial cash, slippage, execution-model version, and reference-method version. Exclude `created_at`. Hash canonical JSON for `run_id`. Before constructing `RunWriter` or creating a run directory, compare this identity and provenance with any existing run and refuse mismatches instead of silently skipping completed sessions.

**Step 4: Generalize target generation over injected history**

Replace the synthetic-only closure with a provider-neutral closure that receives a history lookup. It calls `generate_target` with the decision snapshot and history admitted by that decision timestamp.

Keep the Task 1 synthetic `SimulationFrame` migration intact. The command remains offline and runs all three philosophies; add regression assertions that later market-replay wiring cannot construct an OpenBB source from `demo`.

**Step 5: Add `market-replay`**

Add a thin Typer command plus a testable internal function that accepts `DailyPriceLoader`. The command:

- Uses `config/universes/us-large-cap-30.yaml` plus SPY.
- Loads only `philosophies/trend-v1.yaml`.
- Requires explicit `--start` and `--end` evaluation dates.
- Fetches at least 400 calendar days before `start` for 252-session momentum warmup and enough days after `end` to find a completed execution session.
- Uses `CachedDailyPriceSource(OpenBBYFinancePriceSource(), PriceCache(...))` and receives `PriceFetchResult` so cache hit/miss is explicit rather than inferred.
- Builds weekly frames and no-cost fixed-basket references.
- Runs the existing fake `$100,000` portfolio with existing slippage.
- Emits a data-hash-qualified run ID and all normal evaluation artifacts.
- Prints transport, provider, adjustment, validity classification, and explicit cache hit/miss status.

Do not expose fundamentals, live quotes, streaming, broker, or agent options.

**Step 6: Render provenance in textual reports**

Pass the frozen provenance document into report generation. `report.md` must render validity, transport/provider, adjustment, normalized data hash, fixed-universe survivorship warning, adjusted-fill limitation, benchmark kind, and reference-method version. Update report tests so synthetic and real-market reports cannot be confused.

**Step 7: Update decision/equity export joining**

Decisions now use prior-close dates and equity rows use execution-close dates. Pair ordered decisions and equity points by transition index only after validating equal counts. Do not add execution fields to the frozen decision artifact shape.

**Step 8: Verify engine modes**

Run:

```bash
uv run pytest tests/unit/test_cli.py tests/unit/evaluation/test_report.py tests/integration/test_market_replay.py -v
uv run pytest -q
uv run retailtrader demo --workspace /tmp/retailtrader-synthetic
uv run ruff check src/retailtrader/cli.py src/retailtrader/storage src/retailtrader/evaluation tests/unit/test_cli.py tests/unit/evaluation/test_report.py tests/integration/test_market_replay.py
```

Optional real command:

```bash
uv run --extra data-openbb retailtrader market-replay \
  --workspace /tmp/retailtrader-market \
  --start 2025-01-01 \
  --end 2025-06-30
```

**Step 9: Commit**

```bash
git add src/retailtrader/cli.py src/retailtrader/storage/artifacts.py src/retailtrader/evaluation/report.py tests/unit/test_cli.py tests/unit/evaluation/test_report.py tests/integration/test_market_replay.py
git commit -m "feat: add real-price trend replay command"
```

## Task 7: Export and Render Honest Data Provenance

**Required skills:** Apply `frontend-design` only to preserve the existing visual system; this task changes provenance content, not the interface concept.

**Files:**
- Modify: `src/retailtrader/cli.py`
- Create: `tests/unit/test_cli_export.py`
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/app/page.tsx`

**Step 1: Write failing export tests**

Create synthetic and real-market run fixtures with `data-provenance.json`. Assert exported `runs/data.json` contains a provenance object on every experiment. Assert missing provenance is an error, not a silent default. Assert mixed workspaces preserve different labels.

**Step 2: Verify failure**

Run:

```bash
uv run pytest tests/unit/test_cli_export.py -v
```

Expected: FAIL because provenance is not exported.

**Step 3: Export provenance as engine data**

Include `data-provenance.json` in copied run artifacts and add this typed object to every experiment in the aggregated view model:

```ts
export type DataProvenance = {
  kind: "synthetic" | "real_market";
  validity: "synthetic_demo" | "hindsight_current_universe";
  label: string;
  transport?: string;
  provider: string;
  adjustment: string;
  retrieved_at?: string;
  query_hash?: string;
  normalized_hash?: string;
  benchmark_kind?: "no_cost_reference";
  reference_method_version?: "execution_open_fixed_basket_v1";
  warning?: string;
};
```

No frontend code may infer or calculate provenance.

**Step 4: Render active-run provenance**

Replace the hard-coded `SYNTHETIC DEMO DATA` label with `data.experiments[expIdx].data_provenance.label`. Add transport/provider, adjustment, validity, benchmark kind, and reference method to the existing footer or provenance detail. Switching experiments must switch provenance. Do not enable the locked live-paper toggle or broker control; historical real-data replay is not forward paper trading.

**Step 5: Verify and commit**

Run:

```bash
uv run pytest tests/unit/test_cli_export.py -v
uv run pytest -q
cd frontend && npm ci && npm run build
```

Then:

```bash
git add src/retailtrader/cli.py tests/unit/test_cli_export.py frontend/lib/types.ts frontend/app/page.tsx
git commit -m "feat: display run data provenance"
```

## Task 8: Document Boundaries and Run the Release Gate

**Files:**
- Modify: `README.md`
- Modify: `docs/plans/2026-07-16-agenttrader-lab-design.md`

**Step 1: Update operational documentation**

Document separately:

```bash
uv sync
uv run retailtrader demo --workspace runs/demo
```

and:

```bash
uv sync --extra data-openbb
uv run --extra data-openbb retailtrader market-replay \
  --workspace runs/market \
  --start 2025-01-01 \
  --end 2025-06-30
```

Explain cache paths, immutable cache behavior, 400-day warmup, total-return-adjusted price policy, no separate dividends, prior-close/next-open timing, current-universe survivorship bias, no-cost fixed-basket benchmark references, SPY-calendar gap detection, Yahoo/OpenBB reliability limitations, OpenBB’s AGPL distribution boundary, separate Yahoo data rights, and the opt-in live test.

State explicitly: real prices do not mean real trading. This remains fake-money historical simulation with no broker, real order, fundamentals, or LLM decision path.

Add implementation notes to the approved design for the conservative 16:00 availability convention and early-close limitation.

**Step 2: Run the complete offline gate**

Run:

```bash
uv sync
uv run ruff check .
uv run pytest -q
```

Expected: all checks pass; the external live smoke test skips clearly.

**Step 3: Run the optional provider gate**

Run:

```bash
uv sync --extra data-openbb
RETAILTRADER_LIVE_DATA=1 uv run --extra data-openbb \
  pytest -m integration tests/integration/data/test_openbb_live.py -v
```

Expected: PASS against OpenBB/Yahoo, or a clear upstream diagnostic with no fabricated fallback.

**Step 4: Run both artifact pipelines and frontend build**

Run:

```bash
uv run retailtrader demo --workspace runs/demo
uv run retailtrader export --workspace runs/demo --out frontend/public/runs
cd frontend && npm ci && npm run build
```

If the live provider gate passed, also run and export the market replay to a separate output directory.

**Step 5: Verify repository invariants**

Run:

```bash
git diff -- src/retailtrader/domain.py
git status --short
git diff --check
```

Expected: no `domain.py` diff; generated caches/runs/frontend output remain ignored; no whitespace errors.

**Step 6: Commit documentation**

```bash
git add README.md docs/plans/2026-07-16-agenttrader-lab-design.md
git commit -m "docs: explain real market replay boundaries"
```

## Final Review Checklist

- [ ] `src/retailtrader/domain.py` is unchanged.
- [ ] Default install and test suite do not install OpenBB or use the network.
- [ ] OpenBB and yfinance provider choices are explicit.
- [ ] Every ingested daily bar has open and close availability metadata before snapshot conversion.
- [ ] Decision history passes both session and close-availability gates.
- [ ] Fills use the next session open and marks use that session close.
- [ ] Order quantities are timestamped at execution open, never backdated to decision close.
- [ ] Committed transition journals recover byte-identical artifacts after injected crashes.
- [ ] Run identity includes all calculation inputs and frozen data hash, excludes `created_at`, and refuses incompatible resume.
- [ ] Cache entries are complete-directory atomic, immutable, concurrent-writer-safe, and integrity-checked.
- [ ] Strategy and references use the same adjustment policy; dividends are not double-counted.
- [ ] Real replay is trend-only and labeled as current-universe hindsight.
- [ ] SPY/equal-weight values are labeled no-cost fixed-basket references based at first execution open.
- [ ] Synthetic runs remain offline and visibly synthetic.
- [ ] Real-price runs remain fake-money only.
