# RetailTrader Trading Philosophy Lab Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a deterministic US equities philosophy-testing system that supports versioned YAML strategies, historical replay, isolated forward paper portfolios, performance comparison, a static demo frontend, and optional Pi-generated explanations.

**Architecture:** A Python engine converts validated philosophy specifications into target portfolio weights using point-in-time data. Historical replay and forward paper trading share one event-sourced portfolio transition path. Pi may author YAML and explain results but cannot calculate signals, alter risk controls, or execute trades.

**Tech Stack:** Python 3.12, uv, Pydantic 2, PyYAML, pandas, NumPy, PyArrow, Typer, Rich, OpenBB adapter, pytest, Ruff, mypy, Pi RPC.

---

## Product Boundaries

Version 0 supports:

- US large-cap stocks.
- A fixed 30-symbol demo universe.
- Daily bars. Weekly rebalancing for the demo configuration (monthly supported) so the replay timeline has enough events to scrub.
- A static frontend that reads run artifacts: philosophy switcher, replay timeline, comparison view.
- Long-only portfolios.
- Internal virtual paper portfolios.
- Quality-value, GARP, and trend templates.
- Restricted custom YAML philosophies.
- Historical replay and forward paper updates.
- SPY and equal-weight benchmarks.
- Markdown, JSON, JSONL, CSV, and Parquet artifacts.

Version 0 excludes:

- Broker connectivity.
- Real-money trading.
- Intraday execution.
- Shorting and leverage.
- LLM-selected trades.
- Strategy optimization.
- Sentiment factors.
- Bull/base/bear scenario generation ("future prediction") — deferred to v1; see Migration Signals.
- Dynamic index constituents.
- Flue integration beyond stable CLI and artifact contracts.

## Repository Shape

```text
RetailTrader/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── .gitignore
├── .pi/
│   ├── SYSTEM.md
│   └── skills/experiment-analysis/SKILL.md
├── config/
│   └── universes/us-large-cap-30.yaml
├── philosophies/
│   ├── quality-value-v1.yaml
│   ├── garp-v1.yaml
│   └── trend-v1.yaml
├── frontend/
│   ├── package.json
│   ├── app/
│   ├── components/
│   ├── lib/
│   └── public/runs/
├── src/retailtrader/
│   ├── cli.py
│   ├── domain.py
│   ├── philosophy.py
│   ├── factors.py
│   ├── scoring.py
│   ├── allocation.py
│   ├── data/
│   │   ├── protocol.py
│   │   ├── cache.py
│   │   └── openbb.py
│   ├── simulation/
│   │   ├── execution.py
│   │   ├── ledger.py
│   │   └── runner.py
│   ├── evaluation/
│   │   ├── metrics.py
│   │   └── report.py
│   ├── storage/
│   │   ├── artifacts.py
│   │   └── events.py
│   └── agents/pi_reporter.py
├── tests/
│   ├── fixtures/
│   ├── unit/
│   ├── integration/
│   └── e2e/
└── docs/
    ├── decisions/
    └── plans/
```

## Core Invariants

1. The deterministic engine is the only component allowed to calculate scores, target weights, orders, fills, cash, or positions.
2. Pi may help author a philosophy and explain artifacts, but it cannot mutate an experiment or influence execution after activation.
3. Every observation must have an `available_at` timestamp no later than the decision timestamp.
4. Historical replay and forward paper trading must call the same portfolio transition function.
5. An active philosophy is immutable. Any edit creates a new version and experiment.
6. Every order, fill, and portfolio transition is append-only and reconstructable.
7. The MVP never connects to a broker or sends a real order.

## Task 1: Bootstrap The Project

**Files:**

- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md`
- Create: `AGENTS.md`
- Create: `src/retailtrader/__init__.py`
- Create: `tests/__init__.py`
- Create: `docs/decisions/0001-data-provider-and-license.md`

**Step 1: Initialize Git**

Run:

```bash
git init
```

Expected: an empty Git repository initialized in `RetailTrader`.

**Step 2: Create the package manifest**

Configure Python 3.12 and uv. Add Pydantic, PyYAML, pandas, NumPy, PyArrow, Typer, and Rich as runtime dependencies. Put OpenBB in an optional `data-openbb` dependency group because of its installation size and AGPL license. Add pytest, Ruff, and mypy as development dependencies.

Register the CLI entry point:

```toml
[project.scripts]
retailtrader = "retailtrader.cli:app"
```

**Step 3: Add repository safeguards**

Ignore `.env`, provider credentials, `data/cache/`, `runs/`, `.pi/sessions/`, Python caches, coverage output, and virtual environments.

Document these non-negotiable rules in `AGENTS.md`:

```text
No real-money trading.
No broker integration in v0.
No LLM-generated order decisions.
No historical observation without an availability timestamp.
No separate backtest and paper-trading calculation paths.
```

**Step 4: Record the licensing boundary**

Document that OpenBB is an optional AGPL dependency and that provider data rights are separate from source-code licensing. Require a legal/product decision before distributing a proprietary build containing it.

**Step 5: Verify the scaffold**

Run:

```bash
uv sync
uv run python -c "import retailtrader"
uv run pytest --collect-only
uv run ruff check .
uv run mypy src
```

Expected: all commands exit successfully and pytest reports zero collected tests.

**Step 6: Commit**

```bash
git add pyproject.toml .gitignore README.md AGENTS.md src tests docs
git commit -m "chore: scaffold retail trader philosophy lab"
```

## Task 2: Define Immutable Domain Contracts

**Files:**

- Create: `src/retailtrader/domain.py`
- Create: `tests/unit/test_domain.py`

Define these Pydantic models:

```text
PhilosophySpec
ExperimentManifest
MarketBar
FundamentalObservation
MarketSnapshot
FactorObservation
TargetPosition
TargetPortfolio
OrderIntent
FillEvent
PortfolioSnapshot
EvaluationReport
```

Every persisted artifact must include:

```text
schema_version
run_id
created_at
as_of
source references
engine version
content hash
```

**Step 1: Write failing validation tests**

```python
def test_rejects_future_fundamental_observation():
    with pytest.raises(ValidationError):
        FundamentalObservation(
            symbol="AAPL",
            metric="revenue",
            value=100,
            period_end=date(2026, 6, 30),
            available_at=datetime(2026, 8, 1, tzinfo=UTC),
            as_of=datetime(2026, 7, 16, tzinfo=UTC),
        )


def test_rejects_leveraged_target_portfolio():
    with pytest.raises(ValidationError):
        TargetPortfolio(
            run_id="run-1",
            as_of=datetime(2026, 7, 16, tzinfo=UTC),
            cash_weight=0,
            positions=[
                TargetPosition(symbol="AAPL", weight=0.7),
                TargetPosition(symbol="MSFT", weight=0.7),
            ],
        )
```

**Step 2: Run tests and confirm failure**

Run: `uv run pytest tests/unit/test_domain.py -v`

Expected: FAIL because `retailtrader.domain` and its models do not exist.

**Step 3: Implement minimal models and validators**

Add validation for unique symbols, positive prices and quantities, `available_at <= as_of`, non-negative cash, valid target-weight totals, immutable philosophy identity, and no short or leveraged weights.

Use UTC-aware datetimes exclusively.

**Step 4: Run tests**

Run: `uv run pytest tests/unit/test_domain.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/retailtrader/domain.py tests/unit/test_domain.py
git commit -m "feat: define immutable experiment contracts"
```

## Task 3: Implement Philosophy YAML Validation

**Files:**

- Create: `src/retailtrader/philosophy.py`
- Create: `tests/unit/test_philosophy.py`
- Create: `philosophies/quality-value-v1.yaml`
- Create: `philosophies/garp-v1.yaml`
- Create: `philosophies/trend-v1.yaml`
- Create: `config/universes/us-large-cap-30.yaml`

Support only audited fields:

```text
universe
rebalance cadence
eligibility filters
weighted factors
factor direction
minimum factor coverage
top-N selection
cash buffer
maximum position weight
turnover constraint
```

Support only these filter operators:

```text
gt
gte
lt
lte
eq
between
```

**Step 1: Write failing parser tests**

Test valid loading, unknown metrics, arbitrary expressions, negative weights, unsupported operators, duplicate factors, unknown YAML keys, missing portfolio controls, and deterministic content hashing.

**Step 2: Run tests and confirm failure**

Run: `uv run pytest tests/unit/test_philosophy.py -v`

Expected: FAIL because the parser does not exist.

**Step 3: Implement strict parsing**

Use Pydantic models with `extra="forbid"`. Load YAML through `yaml.safe_load`. Normalize the specification and hash its canonical JSON representation. Do not evaluate Python expressions or import code from YAML.

**Step 4: Add templates**

Create three measurably distinct templates:

```text
quality-value: ROIC, FCF yield, leverage, FCF consistency
GARP: revenue growth, EPS growth, growth-adjusted P/E, leverage
trend: 6-month momentum, 12-month momentum, 200-day trend, volatility
```

Label the templates as inspired by public investment principles, not replicas of named investors.

**Step 5: Verify templates**

Run:

```bash
uv run pytest tests/unit/test_philosophy.py -v
uv run retailtrader philosophy validate philosophies/quality-value-v1.yaml
uv run retailtrader philosophy validate philosophies/garp-v1.yaml
uv run retailtrader philosophy validate philosophies/trend-v1.yaml
```

Expected: all three specifications are valid and receive stable hashes.

**Step 6: Commit**

```bash
git add src/retailtrader/philosophy.py tests/unit/test_philosophy.py philosophies config
git commit -m "feat: add validated philosophy specifications"
```

## Task 4: Build The Point-In-Time Data Boundary

**Files:**

- Create: `src/retailtrader/data/__init__.py`
- Create: `src/retailtrader/data/protocol.py`
- Create: `src/retailtrader/data/cache.py`
- Create: `src/retailtrader/data/openbb.py`
- Create: `tests/unit/data/test_cache.py`
- Create: `tests/unit/data/test_point_in_time.py`
- Create: `tests/integration/data/test_openbb.py`
- Create: `tests/fixtures/market_data/`

Define the provider boundary:

```python
class MarketDataProvider(Protocol):
    def prices(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
    ) -> pd.DataFrame: ...

    def fundamentals(
        self,
        symbols: Sequence[str],
        as_of: datetime,
    ) -> list[FundamentalObservation]: ...
```

**Point-in-time strategy (decided 2026-07-16):** Free and low-cost providers generally do not expose filing acceptance timestamps. When a provider supplies a real acceptance timestamp, use it. Otherwise approximate `available_at = period_end + 45 days` and record on each observation which path produced it. The trend philosophy replays on live price data from day one (prices need no fundamentals); quality-value and GARP replay on synthetic fixtures until the lag approximation is spot-checked against several real SEC filing dates. Record this decision in `docs/decisions/0002-point-in-time-approximation.md`.

**Step 1: Write point-in-time tests**

Test that a filing accepted after `as_of` is excluded, the latest eligible filing wins, provider identity is retained, cache keys include all query parameters, and raw payload hashes remain stable.

**Step 2: Run tests and confirm failure**

Run: `uv run pytest tests/unit/data -v`

Expected: FAIL because the provider and cache do not exist.

**Step 3: Implement the protocol and file cache**

Cache normalized data under:

```text
data/cache/<provider>/<query-hash>.parquet
data/cache/<provider>/<query-hash>.metadata.json
```

Metadata must record provider, retrieval time, query, adjustment policy, source references, and raw hash.

**Step 4: Implement the OpenBB adapter**

Use explicit providers rather than OpenBB defaults. Normalize lowercase provider columns to the internal schema. For fundamentals, retain filing acceptance timestamps when the provider supplies them, apply the 45-day availability approximation otherwise, and reject observations unavailable at `as_of`.

Use adjusted daily OHLCV consistently for the research simulator and document that corporate actions are represented through adjusted prices. Do not separately add dividends when using total-return-adjusted data.

**Step 5: Keep network calls out of unit tests**

Use deterministic synthetic fixtures. Mark live tests with `@pytest.mark.integration` and skip them unless required provider credentials and an explicit opt-in variable are present.

**Step 6: Verify**

Run:

```bash
uv run pytest tests/unit/data -v
RETAILTRADER_LIVE_DATA=1 uv run pytest -m integration tests/integration/data -v
```

Expected: unit tests pass offline. Integration tests either pass with configured access or skip with a clear reason.

**Step 7: Commit**

```bash
git add src/retailtrader/data tests/unit/data tests/integration/data tests/fixtures
git commit -m "feat: add point-in-time market data boundary"
```

## Task 5: Implement Audited Factors

**Files:**

- Create: `src/retailtrader/factors.py`
- Create: `tests/unit/test_factors.py`

Implement this initial catalog:

```text
roic
fcf_yield
debt_to_ebitda
free_cash_flow_consistency
revenue_growth_3y
eps_growth_3y
growth_adjusted_pe
momentum_6m
momentum_12m
above_sma_200
volatility_60d
```

**Step 1: Write fixed numerical tests**

For every factor, provide a small input with a hand-calculated expected result. Add tests for missing values, zero denominators, insufficient lookback, negative earnings, and future observations.

**Step 2: Run tests and confirm failure**

Run: `uv run pytest tests/unit/test_factors.py -v`

Expected: FAIL because factor functions do not exist.

**Step 3: Implement pure factor functions**

Every factor must return a `FactorObservation` containing the metric, value or unavailable reason, formula version, source observation references, and `as_of` timestamp.

Missing data must never silently become zero. Price factors must exclude the execution bar. Fundamental factors must consume only observations available by `as_of`.

**Step 4: Run tests**

Run: `uv run pytest tests/unit/test_factors.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/retailtrader/factors.py tests/unit/test_factors.py
git commit -m "feat: implement deterministic factor catalog"
```

## Task 6: Score Securities And Produce Target Weights

**Files:**

- Create: `src/retailtrader/scoring.py`
- Create: `src/retailtrader/allocation.py`
- Create: `tests/unit/test_scoring.py`
- Create: `tests/unit/test_allocation.py`

Use this fixed processing order:

```text
eligibility
→ factor availability
→ cross-sectional normalization
→ weighted score
→ deterministic ranking
→ top-N selection
→ equal-weight allocation
→ risk constraints
→ target portfolio
```

**Step 1: Write failing scoring tests**

Test score direction, factor weighting, missing-factor coverage, stable symbol tie-breaking, and deterministic ranking regardless of input order.

**Step 2: Write failing allocation tests**

Test equal weighting, maximum 15% positions, minimum 5% cash, no shorting, no leverage, configurable turnover limits, and exclusion below minimum factor coverage.

**Step 3: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/unit/test_scoring.py tests/unit/test_allocation.py -v
```

Expected: FAIL because scoring and allocation do not exist.

**Step 4: Implement scoring and allocation**

Use cross-sectional percentile ranks. Use ascending symbol as the deterministic tie-breaker. Include score attribution for selected and rejected securities.

Version 0 uses equal weights after ranking. Do not add numerical optimization.

**Step 5: Run tests**

Run:

```bash
uv run pytest tests/unit/test_scoring.py tests/unit/test_allocation.py -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/retailtrader/scoring.py src/retailtrader/allocation.py tests/unit/test_scoring.py tests/unit/test_allocation.py
git commit -m "feat: generate constrained target portfolios"
```

## Task 7: Create The Event-Sourced Paper Simulator

**Files:**

- Create: `src/retailtrader/simulation/__init__.py`
- Create: `src/retailtrader/simulation/execution.py`
- Create: `src/retailtrader/simulation/ledger.py`
- Create: `src/retailtrader/storage/__init__.py`
- Create: `src/retailtrader/storage/events.py`
- Create: `tests/unit/simulation/test_execution.py`
- Create: `tests/unit/simulation/test_ledger.py`

Execution rules:

```text
Signals are calculated after session close.
Orders fill at the next available session open.
Slippage is configurable in basis points.
Sells execute before buys.
Shares are integers.
Buys cannot create negative cash.
Symbols process in stable order.
Every order and fill appends to JSONL.
Repeated processing of the same session is idempotent.
```

Ledger event types:

```text
portfolio_created
target_generated
order_created
order_rejected
order_filled
portfolio_marked
rebalance_completed
```

**Step 1: Write failing execution tests**

Cover next-open execution, slippage, sell-before-buy ordering, integer quantities, insufficient cash, stable ordering, and duplicate rebalance rejection.

**Step 2: Write failing ledger reconstruction tests**

Create a short event sequence and assert that replay reconstructs cash, positions, cost basis, and equity exactly.

**Step 3: Run tests and confirm failure**

Run: `uv run pytest tests/unit/simulation -v`

Expected: FAIL because the simulator does not exist.

**Step 4: Implement the execution model and ledger**

Keep all arithmetic deterministic. Use `Decimal` for cash and fill accounting. Serialize decimal values as strings in JSON artifacts.

**Step 5: Run tests**

Run: `uv run pytest tests/unit/simulation -v`

Expected: PASS.

**Step 6: Commit**

```bash
git add src/retailtrader/simulation src/retailtrader/storage tests/unit/simulation
git commit -m "feat: add deterministic virtual portfolio ledger"
```

## Task 8: Unify Historical Replay And Forward Paper Trading

**Files:**

- Create: `src/retailtrader/simulation/runner.py`
- Create: `src/retailtrader/storage/artifacts.py`
- Create: `tests/integration/test_replay.py`
- Create: `tests/integration/test_forward_paper.py`

Expose one portfolio transition:

```python
def step(
    experiment: ExperimentManifest,
    portfolio: PortfolioSnapshot,
    snapshot: MarketSnapshot,
) -> PortfolioSnapshot:
    ...
```

Historical replay loops over snapshots. Forward paper trading invokes the same transition once for each newly completed session.

Persist:

```text
manifest.json
philosophy.yaml
decisions.jsonl
orders.jsonl
fills.jsonl
portfolio.jsonl
equity.csv
```

**Step 1: Write a failing replay test**

Use deterministic fixture data to replay several rebalances and assert exact final cash, positions, fills, and equity.

**Step 2: Write a failing replay-versus-forward parity test**

Feed identical snapshots one at a time through forward mode and all at once through replay mode. Assert byte-equivalent event payloads after excluding creation timestamps.

**Step 3: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/integration/test_replay.py tests/integration/test_forward_paper.py -v
```

Expected: FAIL because the runner does not exist.

**Step 4: Implement the shared runner**

An experiment resume must verify philosophy hash, universe hash, engine version, adjustment policy, execution model, and last processed timestamp. Refuse to resume when any immutable input differs.

**Step 5: Run tests**

Run:

```bash
uv run pytest tests/integration/test_replay.py tests/integration/test_forward_paper.py -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/retailtrader/simulation/runner.py src/retailtrader/storage/artifacts.py tests/integration
git commit -m "feat: unify replay and forward paper execution"
```

## Task 9: Add Evaluation And Comparison

**Files:**

- Create: `src/retailtrader/evaluation/__init__.py`
- Create: `src/retailtrader/evaluation/metrics.py`
- Create: `src/retailtrader/evaluation/report.py`
- Create: `tests/unit/evaluation/test_metrics.py`
- Create: `tests/unit/evaluation/test_report.py`

Calculate:

```text
total return
CAGR
annualized volatility
Sharpe ratio
maximum drawdown
turnover
trade count
average holding period
cash exposure
maximum concentration
SPY-relative return
equal-weight-relative return
```

Add philosophy-fidelity metrics:

```text
factor coverage
constraint interventions
ranking churn
selection stability
rule violations
```

Generate:

```text
evaluation.json
report.md
equity.csv
holdings.csv
comparison.md
```

**Step 1: Write failing metric tests**

Use hand-calculated equity curves for total return, drawdown, annualized volatility, and turnover. Explicitly test zero-volatility and insufficient-history cases.

**Step 2: Write failing report tests**

Assert that reports include experiment identity, immutable inputs, benchmark comparisons, missing data, constraint interventions, and the research-only disclaimer.

**Step 3: Run tests and confirm failure**

Run: `uv run pytest tests/unit/evaluation -v`

Expected: FAIL because evaluation does not exist.

**Step 4: Implement metrics and deterministic Markdown reports**

Reports must state that historical replay is descriptive, a fixed demo universe introduces selection bias, short paper periods do not establish an edge, and results are not financial advice.

**Step 5: Run tests**

Run: `uv run pytest tests/unit/evaluation -v`

Expected: PASS.

**Step 6: Commit**

```bash
git add src/retailtrader/evaluation tests/unit/evaluation
git commit -m "feat: evaluate and compare philosophy experiments"
```

## Task 10: Build The CLI And Demo Workflow

**Files:**

- Create: `src/retailtrader/cli.py`
- Create: `tests/unit/test_cli.py`
- Create: `tests/e2e/test_demo.py`

Commands:

```text
retailtrader philosophy validate
retailtrader data fetch
retailtrader experiment create
retailtrader experiment replay
retailtrader paper step
retailtrader experiment evaluate
retailtrader experiment compare
retailtrader demo
```

All commands must support:

```text
--workspace
--run-id
--format text|json
```

Machine-readable JSON output and stable exit codes form the future Flue integration contract.

**Step 1: Write failing CLI tests**

Test help output, validation errors, JSON output, stable non-zero exit codes, explicit workspace selection, and idempotent paper steps.

**Step 2: Write the failing end-to-end demo test**

The demo must validate all three philosophies, create three isolated experiments, replay identical dates, produce different target portfolios, generate benchmark comparisons, and reconstruct every portfolio from its event log.

**Step 3: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/unit/test_cli.py tests/e2e/test_demo.py -v
```

Expected: FAIL because the CLI does not exist.

**Step 4: Implement commands and the demo workflow**

The default demo uses deterministic fixture data. Live data requires an explicit flag so the acceptance suite remains repeatable and offline.

**Step 5: Run tests and smoke the CLI**

Run:

```bash
uv run pytest tests/unit/test_cli.py tests/e2e/test_demo.py -v
uv run retailtrader demo --workspace runs/demo
```

Expected: three completed experiments and one comparison report.

**Step 6: Commit**

```bash
git add src/retailtrader/cli.py tests/unit/test_cli.py tests/e2e/test_demo.py
git commit -m "feat: add philosophy experiment CLI"
```

## Task 11: Add Read-Only Pi Reporting

> **Deferrable:** nothing downstream depends on this task. If the demo is time-boxed, skip to Task 12 and 13, and return here after the frontend ships.

**Files:**

- Create: `.pi/SYSTEM.md`
- Create: `.pi/skills/experiment-analysis/SKILL.md`
- Create: `src/retailtrader/agents/__init__.py`
- Create: `src/retailtrader/agents/pi_reporter.py`
- Create: `tests/unit/agents/test_pi_reporter.py`

Pi responsibilities:

```text
Explain factor attribution.
Compare philosophy performance.
Identify missing evidence.
Summarize drawdowns and constraint interventions.
Produce readable Markdown.
```

Pi restrictions:

```text
Read-only tools only.
No bash, edit, or write.
No portfolio calculations.
No order generation.
No modification of experiment artifacts.
The deterministic engine remains the source of all numerical claims.
```

Use Pi RPC:

```bash
pi \
  --mode rpc \
  --tools read,grep,find,ls \
  --session-dir <workspace>/pi-sessions
```

**Step 1: Write a fake-process RPC test**

Test LF-delimited JSON parsing, separate stderr capture, `get_state` readiness, `set_session_name`, prompt acceptance, `agent_end` completion, abort, SIGTERM fallback, and early EOF failure.

**Step 2: Run tests and confirm failure**

Run: `uv run pytest tests/unit/agents/test_pi_reporter.py -v`

Expected: FAIL because the RPC client does not exist.

**Step 3: Implement the RPC reporter**

Do not use the unsupported `--name` CLI flag. Set the display name using the RPC `set_session_name` command. Treat the prompt response as acceptance only and wait for `agent_end` before collecting final text.

The Python process writes returned Markdown to disk after validating that numerical values cited by Pi exist in deterministic artifacts.

**Step 4: Run tests**

Run: `uv run pytest tests/unit/agents/test_pi_reporter.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add .pi src/retailtrader/agents tests/unit/agents
git commit -m "feat: add read-only Pi experiment reporter"
```

## Task 12: Complete Documentation And Acceptance

**Files:**

- Modify: `README.md`
- Modify: `AGENTS.md`
- Create: `docs/architecture.md`
- Create: `docs/data-integrity.md`
- Create: `docs/experiment-lifecycle.md`

**Step 1: Document the user workflow**

Cover philosophy validation, experiment creation, historical replay, daily paper updates, comparison reports, artifact export and frontend build, artifact locations, and safe cleanup.

**Step 2: Document known limitations**

Include fixed-universe selection bias, provider corrections and restatements, adjusted-price execution simplifications, lack of broker realism, short paper periods, and the absence of sentiment and transaction-level liquidity.

**Step 3: Run full verification**

Run:

```bash
uv run ruff check .
uv run mypy src
uv run pytest -q
uv run retailtrader demo --workspace runs/acceptance
git status --short
```

Expected:

```text
Ruff: no errors
mypy: no errors
pytest: all tests pass
demo: three completed experiments and one comparison report
git status: only intended source and documentation changes
```

**Step 4: Commit**

```bash
git add README.md AGENTS.md docs
git commit -m "docs: document philosophy lab operation"
```

## Task 13: Build The Demo Frontend

**Files:**

- Modify: `src/retailtrader/cli.py` (add `retailtrader export`)
- Create: `tests/unit/test_export.py`
- Create: `frontend/` (Next.js, TypeScript, static export)

The frontend has no backend server. It reads run artifacts exported as static files — the artifacts are the API. This keeps the demo deployable anywhere and preserves the engine as the single source of truth.

**Export contract:**

```text
retailtrader export --workspace runs/demo --out frontend/public/runs
```

Copies per experiment: `manifest.json`, `philosophy.yaml`, `equity.csv`, `decisions.jsonl`, `portfolio.jsonl`, `evaluation.json`, and writes a top-level `index.json` listing all exported experiments with identity, philosophy name, version, and date range.

**Panels:**

```text
1. Philosophy switcher — select any exported experiment; swap between
   philosophies over the same period without reloading.
2. Replay timeline — equity curve versus SPY and equal-weight benchmarks
   with a marker per rebalance event. Selecting a marker shows that date's
   decisions from decisions.jsonl: selections, rejections, and factor
   attribution.
3. Comparison view — evaluation metrics table across all experiments and
   benchmarks, philosophy-fidelity metrics, and the research-only
   disclaimer rendered visibly, not in a footer.
```

**Step 1: Write failing export tests**

Test that export copies every artifact for each experiment, writes a complete `index.json`, fails with a stable exit code on a missing or incomplete run, and is idempotent.

**Step 2: Run tests and confirm failure**

Run: `uv run pytest tests/unit/test_export.py -v`

Expected: FAIL because the export command does not exist.

**Step 3: Implement export and confirm tests pass**

Run: `uv run pytest tests/unit/test_export.py -v`

Expected: PASS.

**Step 4: Scaffold the frontend**

Create the Next.js app with TypeScript and `output: 'export'` in `next.config`. Parse CSV and JSONL artifacts client-side in `frontend/lib/`. No server routes, no database, no credentials.

**Step 5: Implement the three panels**

Keep charting to one library. Render decimal strings exactly as emitted by the engine; the frontend must not recalculate returns, weights, or metrics — it only displays engine artifacts (Core Invariant 1 applies to the frontend too).

**Step 6: Verify end to end**

Run:

```bash
uv run retailtrader demo --workspace runs/demo
uv run retailtrader export --workspace runs/demo --out frontend/public/runs
cd frontend && npm run build && npx serve out
```

Expected: the static build succeeds, all three demo experiments appear in the switcher, timeline markers show per-date decisions, and the comparison table matches `evaluation.json` values exactly.

**Step 7: Commit**

```bash
git add src/retailtrader/cli.py tests/unit/test_export.py frontend
git commit -m "feat: add static demo frontend with export contract"
```

## Migration Signals

Add scenario generation (bull/base/bear "prediction") when:

- The lab demo works end to end and the frontend has shipped.
- An assumptions-and-probabilities schema is agreed and every output is labeled hypothesis, never evidence.
- Pi drafts scenario assumptions while the deterministic engine computes expected values, preserving Core Invariant 1.

Add Flue when:

- Manual CLI execution is reliable.
- Scheduled forward-paper updates are needed.
- Multiple experiment workspaces must run concurrently.
- Cancellation, process isolation, and run-status APIs are required.

Add broker paper trading when:

- Internal ledgers reconcile deterministically.
- Corporate actions and market calendars are covered.
- A selected philosophy survives a predefined paper period.
- Broker execution can be mirrored without becoming the source of portfolio truth.

Add a richer strategy language when:

- The audited factor catalog blocks legitimate custom philosophies.
- Users repeatedly request the same missing operator or transformation.
- A sandboxed compiler can preserve determinism and produce reviewable YAML.

## 3-Hour Demo Build Mode

Scope cuts for the time-boxed build (all restorable later; the full task text above remains the reference):

```text
Skip Task 4's live OpenBB adapter — synthetic fixture provider only.
  The demo already defaults to fixture data (Task 10).
Skip Task 11 (Pi reporter) — already marked deferrable.
Reduce testing to one focused test file per module covering the
  invariants (PIT rejection, no-leverage, ledger reconstruction,
  replay determinism). Keep ruff; drop mypy if it fights the clock.
```

Execution phases:

```text
Phase 0 — main worktree, sequential (~25 min):
  Task 1 + Task 2, then hand-write one realistic fixture run under
  tests/fixtures/demo-run/ (manifest.json, equity.csv, decisions.jsonl,
  portfolio.jsonl, evaluation.json, index.json). This freezes the
  artifact contract every track builds against.

Phase 1 — three parallel worktrees (~90 min):
  WT-A engine:     Task 3 → Task 5 → Task 6
  WT-B simulation: Task 7 → Task 8 → Task 9
  WT-C frontend:   Task 13 against the fixture artifacts
  Tracks touch disjoint directories; no shared files beyond domain.py,
  which is frozen after Phase 0.

Phase 2 — main worktree, merge and wire (~45 min):
  Merge WT-A and WT-B → Task 10 CLI and demo → export real artifacts →
  point the frontend at them → end-to-end smoke → light Task 12 docs.
```

Frontend plating rules for the demo:

```text
Locked-but-visible features are allowed and encouraged:
  "Scenarios" tab with a v1 badge, disabled "Connect broker" button,
  grayed "Live paper mode" toggle.
Fabricated computed values are forbidden: no fake predictions, fake
  live prices, or fake track records. Core Invariant 1 extends to
  plating — if a number looks computed, the engine computed it.
Badge the UI "synthetic demo data" once in the header.
```

## The 80/20 Hedge

Keep these contracts independent from OpenBB, Pi, and Flue:

```text
MarketDataProvider
PhilosophySpec
TargetPortfolio
FillEvent
PortfolioSnapshot
ExperimentManifest
EvaluationReport
```

This permits replacing OpenBB, adding Flue supervision, or mirroring orders to Alpaca without rewriting philosophy definitions or historical experiments.
