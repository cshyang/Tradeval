# AgentTrader First Vertical Slice Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let a user create an AI-interpreted Buffett-inspired agent, configure $100,000 of fake capital and a screened US large-cap universe, run an immediate hindsight scenario, inspect every proposal and risk intervention, and start an automatic forward-paper experiment.

**Architecture:** AgentTrader is a new sibling TypeScript service using Pi in-process for philosophy and proposal generation. RetailTrader remains the deterministic market-data, screening, risk, simulation, ledger, and evaluation service; the services exchange versioned immutable JSON artifacts and RetailTrader CLI JSON envelopes. The existing Next.js UI becomes the unified product shell, while Pi workers expose a Flue-ready NDJSON worker contract without requiring Flue in v0.

**Tech Stack:** Python 3.12, Pydantic 2, Typer, OpenBB/Yahoo prices, SEC EDGAR company facts, Node.js 22, TypeScript 5, `@mariozechner/pi-ai` 0.73.1, `@mariozechner/pi-agent-core` 0.73.1, TypeBox 0.34.52, Hono 4.12.30, `@hono/node-server` 2.0.10, Vitest 4.1.10, Next.js 15.5.20, pytest, Ruff.

---

## Repository Layout

```text
Exploration/
├── RetailTrader/                 # deterministic trust boundary
│   ├── src/retailtrader/agent/
│   ├── frontend/
│   └── docs/plans/
└── AgentTrader/                  # AI and experiment orchestration service
    ├── src/contracts/
    ├── src/pi/
    ├── src/workers/
    ├── src/jobs/
    ├── src/api/
    ├── tests/
    └── package.json
```

AgentTrader and RetailTrader are one user-facing product but separate services. No Pi tool may write RetailTrader run artifacts or calculate fills, cash, positions, returns, or risk metrics.

## Task 0: Reconcile The Active Live-Data Worktree

**Precondition:** Do not start while `.worktrees/live-market-data/.pi-subagents/` has an active worker or reviewer. Wait for the current task to commit and leave no tracked changes.

**Files:**
- Existing branch: `feature/live-market-data`
- Existing plan: `docs/plans/2026-07-16-live-market-data.md`
- Review: `src/retailtrader/simulation/frame.py`
- Review: `src/retailtrader/simulation/runner.py`
- Review: `src/retailtrader/simulation/execution.py`
- Review: `src/retailtrader/storage/transitions.py`
- Review: `src/retailtrader/storage/events.py`
- Review: `src/retailtrader/storage/artifacts.py`
- Review: `src/retailtrader/cli.py`

**Step 1: Record both verified tips**

Run:

```bash
git -C RetailTrader log -1 --oneline main
git -C RetailTrader/.worktrees/live-market-data log -1 --oneline
git -C RetailTrader/.worktrees/live-market-data status --short
```

Expected: the live-data branch is clean except ignored/local `.pi-subagents/`; its active task has a completion report and commit.

**Step 2: Rebase onto current main**

Run from the live-data worktree:

```bash
git rebase main
```

Resolve conflicts by preserving these contracts from both histories:

```text
From live-data:
  SimulationFrame with distinct decision close, execution open, execution close
  immutable per-session transition journals
  atomic deterministic materialization

From main:
  max_turnover enforcement and rejection records
  immutable manifest source/cash/slippage fields
  resume identity and materialized-artifact reconciliation
  synthetic_mega_cap_proxy naming
  lifecycle CLI JSON envelopes and exit codes
  atomic frontend export
```

Do not resolve `runner.py`, `events.py`, `artifacts.py`, or `cli.py` using blanket `--ours` or `--theirs`.

**Step 3: Add conflict-regression tests before implementation changes**

Extend `tests/integration/test_replay_parity.py` to prove one run simultaneously has:

- prior-close-only target generation;
- execution-open fills;
- active turnover capping;
- crash recovery from a committed journal;
- rejection of changed manifest/philosophy inputs;
- byte-identical replay and restarted paper artifacts.

Run the focused test and confirm it fails before reconciliation.

**Step 4: Reconcile implementation**

Make transition journals authoritative. Validate immutable identity before loading journals, rematerialize projections from committed journals, then restore the portfolio. Persist max-turnover interventions inside the journal so rematerialization cannot lose them.

**Step 5: Verify and commit**

Run:

```bash
uv run ruff check .
uv run pytest -q
npm --prefix frontend run build
git diff --check
```

Commit only after all combined invariants pass:

```bash
git commit -am "refactor: reconcile live data with simulation integrity"
```

## Task 1: Finish The RetailTrader Live-Data Prerequisite

Execute the remaining tasks in `docs/plans/2026-07-16-live-market-data.md` after rebasing. Preserve its synthetic offline default and explicit provenance. The acceptance gate for AgentTrader is:

```bash
uv run retailtrader market replay \
  --workspace runs/live-smoke \
  --philosophy philosophies/trend-v1.yaml \
  --provider yfinance \
  --start 2024-01-05 --end 2024-06-28 \
  --format json
```

Expected: one real-price, current-universe hindsight run with immutable cache/data hashes and no synthetic benchmark labels. Network tests remain opt-in; fixture tests remain offline.

Do not add AI decisions in this task.

## Task 2: Scaffold The AgentTrader Service

**Files:**
- Create: `../AgentTrader/package.json`
- Create: `../AgentTrader/package-lock.json`
- Create: `../AgentTrader/tsconfig.json`
- Create: `../AgentTrader/src/index.ts`
- Create: `../AgentTrader/src/config.ts`
- Create: `../AgentTrader/tests/config.test.ts`
- Create: `../AgentTrader/AGENTS.md`
- Create: `../AgentTrader/.gitignore`

**Step 1: Initialize the sibling repository**

Create a Node.js 22 TypeScript package with exact dependencies:

```json
{
  "type": "module",
  "scripts": {
    "build": "tsc -p tsconfig.json",
    "test": "vitest run",
    "typecheck": "tsc -p tsconfig.json --noEmit",
    "serve": "node dist/index.js"
  },
  "dependencies": {
    "@hono/node-server": "2.0.10",
    "@mariozechner/pi-agent-core": "0.73.1",
    "@mariozechner/pi-ai": "0.73.1",
    "@sinclair/typebox": "0.34.52",
    "hono": "4.12.30"
  },
  "devDependencies": {
    "@types/node": "^22",
    "typescript": "^5",
    "vitest": "4.1.10"
  }
}
```

**Step 2: Write failing configuration tests**

Test explicit model provider/model ID, RetailTrader executable/root, workspace root, API port, job timeout, and absence of credentials in serialized artifacts.

**Step 3: Implement minimal validated configuration**

Use environment variables only for credentials. Default to Pi model provider `anthropic`; require an explicit model ID. Do not load RetailTrader's `.env` or Pi coding tools.

**Step 4: Verify**

```bash
cd ../AgentTrader
npm ci
npm test
npm run typecheck
```

**Step 5: Commit**

```bash
git add .
git commit -m "chore: scaffold AgentTrader service"
```

## Task 3: Define Versioned Agent Contracts

**Files:**
- Create: `../AgentTrader/src/contracts/mandate.ts`
- Create: `../AgentTrader/src/contracts/protocol.ts`
- Create: `../AgentTrader/src/contracts/candidates.ts`
- Create: `../AgentTrader/src/contracts/proposals.ts`
- Create: `../AgentTrader/src/contracts/audit.ts`
- Create: `../AgentTrader/src/contracts/hash.ts`
- Create: `../AgentTrader/tests/contracts.test.ts`
- Create: `src/retailtrader/agent/__init__.py`
- Create: `src/retailtrader/agent/contracts.py`
- Create: `tests/unit/agent/test_contracts.py`
- Create: `tests/fixtures/agent/decision-proposal-v1.json`

**Step 1: Freeze the wire fixture**

Create a complete proposal fixture with this shape:

```json
{
  "schema_version": 1,
  "experiment_id": "exp-buffett-001",
  "decision_at": "2025-01-31T21:00:00Z",
  "candidate_set_hash": "sha256:...",
  "agent_protocol_hash": "sha256:...",
  "decisions": [
    {
      "symbol": "AAPL",
      "stance": "buy",
      "confidence": 0.74,
      "desired_weight": 0.08,
      "thesis": "Durable cash generation and high returns on capital",
      "evidence_refs": ["obs-41", "obs-87"],
      "risks": ["valuation compression"],
      "invalidating_conditions": ["ROIC below 20%"],
      "intended_holding_period": "3-5 years"
    }
  ],
  "abstentions": []
}
```

**Step 2: Write failing TypeScript and Python compatibility tests**

Both implementations must accept the fixture, reject extra fields, reject duplicate symbols, require `0 <= confidence <= 1`, require `0 <= desired_weight <= 1`, and compute the same canonical SHA-256 hash.

**Step 3: Implement contracts**

Use frozen TypeBox schemas in AgentTrader and frozen Pydantic models in RetailTrader. Keep `DecisionProposal` separate from `TargetPortfolio`; proposals are untrusted intent.

`MandateSpec` owns capital, market, universe/screener, overrides, cadence, horizon, cash, position, turnover, and drawdown limits. `AgentProtocol` owns provider, model, system prompt hash, recipe, tools, sampling, timeout, and retry count.

**Step 4: Verify and commit in each repository**

```bash
cd ../AgentTrader && npm test && npm run typecheck
cd ../RetailTrader && uv run pytest tests/unit/agent/test_contracts.py -v
```

Commit messages:

```text
feat: define AgentTrader experiment contracts
feat: validate agent proposal contracts
```

## Task 4: Add Point-In-Time Fundamental Evidence For The First 30 Stocks

**Files:**
- Create: `src/retailtrader/data/sec.py`
- Create: `src/retailtrader/data/fundamental_cache.py`
- Create: `src/retailtrader/agent/evidence.py`
- Create: `tests/fixtures/market_data/sec_companyfacts_aapl.json`
- Create: `tests/unit/data/test_sec.py`
- Create: `tests/unit/agent/test_evidence.py`
- Create: `tests/integration/data/test_sec_live.py`
- Modify: `pyproject.toml` only if an HTTP client is not already available

**Step 1: Write failing SEC normalization tests**

Normalize a recorded SEC `companyfacts` fixture into availability-bearing observations using filing timestamps. Cover revenue, net income, operating cash flow, capital expenditure, assets, liabilities/debt, and diluted shares. Reject facts without a filing timestamp.

**Step 2: Implement immutable cache**

Use the same query-key, lock, temporary-write, fsync, and content-hash rules as the live-price cache. Send a configured SEC-compliant User-Agent. Live tests require an explicit opt-in environment variable.

**Step 3: Derive evidence metrics**

At a decision cutoff, derive only supported metrics whose source facts were filed by that cutoff:

```text
revenue growth
free-cash-flow margin
return on assets
debt-to-assets
earnings consistency
price-to-free-cash-flow when shares and price are available
```

Every metric stores source observation IDs, formula version, decision cutoff, and unavailable reason. Never substitute current facts into a historical cutoff.

**Step 4: Verify**

```bash
uv run pytest tests/unit/data/test_sec.py tests/unit/agent/test_evidence.py -v
RETAILTRADER_LIVE_SEC=1 uv run pytest tests/integration/data/test_sec_live.py -v
```

Offline acceptance must pass without network access.

**Step 5: Commit**

```bash
git add src/retailtrader/data src/retailtrader/agent tests pyproject.toml uv.lock
git commit -m "feat: add point-in-time fundamental evidence"
```

## Task 5: Build Candidate Screening And Overrides

**Files:**
- Create: `src/retailtrader/agent/screening.py`
- Modify: `src/retailtrader/cli.py`
- Create: `tests/unit/agent/test_screening.py`
- Modify: `tests/unit/test_cli.py`

**Step 1: Write failing screener tests**

Given the 30-stock universe, test deterministic filtering by minimum price history, dollar volume, evidence coverage, pinned symbols, excluded symbols, and stable score/symbol ordering. Pinned symbols bypass ranking but never data-integrity or supported-security checks.

**Step 2: Implement one price-plus-quality screener**

Return at most 12 candidates with structured data, exclusion reasons, coverage, evidence IDs, and a canonical candidate-set hash. Do not call Pi or an LLM.

**Step 3: Add stable CLI JSON**

```bash
uv run retailtrader agent candidates \
  --experiment mandate.json \
  --decision-at 2025-01-31T21:00:00Z \
  --out candidate-set.json \
  --format json
```

Use existing CLI success/error envelopes and exit classes. Repeating the command with identical cache inputs must produce byte-identical output.

**Step 4: Verify and commit**

```bash
uv run pytest tests/unit/agent/test_screening.py tests/unit/test_cli.py -v
git add src/retailtrader/agent src/retailtrader/cli.py tests
git commit -m "feat: add deterministic agent candidate screening"
```

## Task 6: Implement Deterministic Proposal Adjudication

**Files:**
- Create: `src/retailtrader/agent/adjudication.py`
- Create: `src/retailtrader/agent/generator.py`
- Modify: `src/retailtrader/cli.py`
- Create: `tests/unit/agent/test_adjudication.py`
- Create: `tests/integration/test_agent_step.py`

**Step 1: Write hand-calculated failing tests**

Test accepted, capped, rejected, and deferred proposals. Cover unsupported symbols, duplicate symbols, negative/over-one weights, cash buffer, 12% maximum position, 20% turnover, abstention, missing execution bar, and stable intervention ordering.

**Step 2: Convert proposals to bounded targets**

The adapter preserves the original proposal and emits a separate adjudication artifact:

```json
{
  "symbol": "AAPL",
  "requested_weight": 0.18,
  "bounded_weight": 0.12,
  "disposition": "capped",
  "reason": "maximum position weight"
}
```

Feed only the bounded target into the shared `SimulationFrame` transition. Persist proposal and adjudication hashes in the transition journal.

**Step 3: Add one-step CLI**

```bash
uv run retailtrader agent step \
  --workspace runs/agent-exp \
  --proposal decision-proposal.json \
  --format json
```

Require the proposal candidate hash, decision timestamp, and experiment identity to match the prepared frame. The second identical invocation is a no-op; conflicting content for the same session is an integrity error.

**Step 4: Verify and commit**

```bash
uv run pytest tests/unit/agent/test_adjudication.py tests/integration/test_agent_step.py -v
git add src/retailtrader/agent src/retailtrader/cli.py tests
git commit -m "feat: adjudicate and simulate agent proposals"
```

## Task 7: Implement The Pi Philosophy Worker

**Files:**
- Create: `../AgentTrader/src/pi/model.ts`
- Create: `../AgentTrader/src/pi/run-agent.ts`
- Create: `../AgentTrader/src/workers/philosophy.ts`
- Create: `../AgentTrader/src/prompts/philosophy.ts`
- Create: `../AgentTrader/tests/philosophy-worker.test.ts`
- Create: `../AgentTrader/tests/fixtures/philosophy-events.jsonl`

**Step 1: Write a failing fixture-model test**

The worker accepts natural language and returns an `AI-INTERPRETED` philosophy with principles, screener, cadence, defaults, assumptions, unsupported capabilities, and a deterministic spec hash. “JPMorgan” without a style returns a clarification requirement instead of inventing one institutional strategy.

**Step 2: Use Pi without coding tools**

Instantiate `Agent` from `@mariozechner/pi-agent-core` with an explicit model from `@mariozechner/pi-ai`. Register only one `submit_philosophy` tool with strict parameters. Do not expose bash, read, edit, write, filesystem search, or MCP.

Capture all Pi events, raw assistant messages, model identity, usage, latency, and tool arguments. Apply one repair prompt if the agent ends without a valid submission, then fail explicitly.

**Step 3: Verify cleanup and abort behavior**

Test timeout, abort signal, invalid tool payload, one repair, provider failure, and exact usage capture. Never include API keys in event artifacts.

**Step 4: Verify and commit**

```bash
cd ../AgentTrader
npm test -- philosophy-worker
npm run typecheck
git add src tests
git commit -m "feat: generate AI-interpreted philosophies with Pi"
```

## Task 8: Implement The Pi Proposal Worker

**Files:**
- Create: `../AgentTrader/src/workers/proposal.ts`
- Create: `../AgentTrader/src/prompts/proposal.ts`
- Create: `../AgentTrader/src/tools/candidate-data.ts`
- Create: `../AgentTrader/tests/proposal-worker.test.ts`
- Create: `../AgentTrader/tests/fixtures/proposal-events.jsonl`

**Step 1: Write a failing bounded-context test**

Provide a frozen candidate set and assert the model can access only candidates/evidence in that set. Future or unknown evidence IDs must never enter the prompt or accepted proposal.

**Step 2: Register exactly two tools**

```text
get_candidate_data   batch read-only access to the frozen candidate set
submit_proposals     strict DecisionProposal submission
```

Batch all candidate data in one tool call because Pi executes tools sequentially. The prompt states that the agent controls stance and desired weight but not executable orders or portfolio accounting.

**Step 3: Validate and store raw output**

Reject unknown symbols/evidence references before writing the immutable proposal. One repair attempt may fix structure only; it may not add new evidence. A timeout becomes an explicit abstention event after one configured retry.

**Step 4: Verify and commit**

```bash
cd ../AgentTrader
npm test -- proposal-worker
npm run typecheck
git add src tests
git commit -m "feat: generate evidence-bound proposals with Pi"
```

## Task 9: Add A Flue-Ready Worker Protocol

**Files:**
- Create: `../AgentTrader/src/rpc/protocol.ts`
- Create: `../AgentTrader/src/rpc/worker.ts`
- Create: `../AgentTrader/tests/rpc-worker.test.ts`

**Step 1: Freeze NDJSON request/event/result messages**

```json
{"type":"request","job_id":"job-1","operation":"proposal.generate","payload":{}}
{"type":"progress","job_id":"job-1","stage":"agent_evaluating","completed":4,"total":12}
{"type":"result","job_id":"job-1","status":"ok","artifact_path":"...","content_hash":"..."}
```

Stdout contains protocol lines only. Logs go to stderr. Close stdin after one request in one-shot mode; support persistent mode for direct AgentTrader use.

**Step 2: Test process behavior**

Test malformed JSON, duplicate job ID, timeout, cancellation, early EOF, SIGTERM cleanup, non-zero exit, and no output after terminal result.

**Step 3: Implement direct and supervised launchers**

AgentTrader launches the worker directly in v0. Keep command, environment, workspace, progress, cancellation, and result contracts independent so Flue can become the launcher later without changing HTTP APIs or artifacts.

**Step 4: Verify and commit**

```bash
cd ../AgentTrader
npm test -- rpc-worker
git add src tests
git commit -m "feat: add Flue-ready Pi worker protocol"
```

## Task 10: Orchestrate The Hindsight Experiment

**Files:**
- Create: `../AgentTrader/src/jobs/store.ts`
- Create: `../AgentTrader/src/jobs/events.ts`
- Create: `../AgentTrader/src/jobs/hindsight.ts`
- Create: `../AgentTrader/src/retailtrader/client.ts`
- Create: `../AgentTrader/tests/hindsight-job.test.ts`
- Create: `../AgentTrader/tests/fixtures/retailtrader-cli.ts`

**Step 1: Write the failing end-to-end fixture test**

Use a fake Pi worker and fake RetailTrader CLI. Assert the job emits stages in order, processes monthly decision frames, stores immutable candidate/proposal/adjudication references, and finishes with an evaluation path.

**Step 2: Implement ordinary subprocess client**

Spawn RetailTrader with argument arrays, never shell strings. Parse one JSON envelope from stdout; stream stderr as job logs. Apply timeout/cancellation and capture executable/code revision. Do not import RetailTrader Python modules into AgentTrader.

**Step 3: Implement SQLite job index plus filesystem artifacts**

SQLite indexes job ID, experiment ID, status, stage, timestamps, and artifact paths. Versioned JSON/JSONL files remain the audit source. A restart resumes only from committed RetailTrader transitions and terminal Pi artifacts.

**Step 4: Implement the first workflow**

```text
freeze mandate/protocol
-> create synthetic hindsight run
-> for each monthly frame: candidates -> Pi proposal -> RetailTrader agent step
-> evaluate deterministic/passive controls
-> emit final comparison
```

Classify the run as `HINDSIGHT SCENARIO`, even if all retrieved market data is time-gated, because the model knowledge cutoff is not historical.

**Step 5: Verify and commit**

```bash
cd ../AgentTrader
npm test -- hindsight-job
git add src tests
git commit -m "feat: orchestrate hindsight agent experiments"
```

## Task 11: Add The AgentTrader HTTP API And Progress Stream

**Files:**
- Create: `../AgentTrader/src/api/app.ts`
- Create: `../AgentTrader/src/api/server.ts`
- Create: `../AgentTrader/src/api/routes/experiments.ts`
- Create: `../AgentTrader/src/api/routes/events.ts`
- Create: `../AgentTrader/tests/api.test.ts`

**Step 1: Write failing API tests**

Cover:

```text
POST /experiments/philosophy
POST /experiments
GET  /experiments/:id
GET  /experiments/:id/events
POST /experiments/:id/fork
POST /experiments/:id/cancel
```

Test validation, immutable fork behavior, duplicate idempotency keys, missing jobs, cancellation, and terminal errors.

**Step 2: Implement Hono routes**

Use `@hono/node-server`. Return `202` with a job ID for long-running operations. Use Hono `streamSSE`; stop the loop on `stream.aborted`, unregister listeners on abort, and send monotonic event IDs so clients can reconnect.

**Step 3: Verify graceful shutdown**

On SIGINT/SIGTERM, stop accepting requests, abort active direct workers, flush job state, close SQLite, and exit non-zero if cleanup fails.

**Step 4: Verify and commit**

```bash
cd ../AgentTrader
npm test -- api
npm run build
git add src tests
git commit -m "feat: expose AgentTrader experiment API"
```

## Task 12: Build Describe, Configure, Preview, And Run

**Files:**
- Modify: `frontend/app/page.tsx`
- Modify: `frontend/app/globals.css`
- Modify: `frontend/lib/types.ts`
- Create: `frontend/lib/agenttrader.ts`
- Create: `frontend/components/ExperimentBuilder.tsx`
- Create: `frontend/components/PhilosophyDraft.tsx`
- Create: `frontend/components/ExperimentProgress.tsx`
- Create: `frontend/components/AgentDecisionPanel.tsx`
- Create: `frontend/components/InterventionPanel.tsx`
- Create: `frontend/tests/experiment-builder.spec.ts`

**Required skills:** Use `frontend-design` and `vercel-react-best-practices`. Preserve the v3 editorial visual system; do not add a generic dashboard or nested card grid.

**Step 1: Add browser tests before UI implementation**

Test the complete path:

1. Enter “Buffett-inspired long-term quality value.”
2. Review `AI-INTERPRETED` labeling and assumptions.
3. Configure $100,000, monthly cadence, 12% position, 20% turnover.
4. Preview approximately 12 candidates plus pinned/excluded symbols.
5. Start a hindsight scenario.
6. Observe ordered SSE progress.
7. Inspect one proposal and one deterministic intervention.
8. Open Equity Replay as the result view.
9. Compare against cash, equal weight, and deterministic quality value.
10. Fork the experiment without mutating the original.

**Step 2: Convert the static shell to an API-aware product shell**

Keep the existing exported demo as a fallback route or fixture mode. AgentTrader API URL is explicit runtime configuration. Show actionable connection, validation, job, and artifact errors instead of permanent loading states.

**Step 3: Keep all financial calculations server-side**

The UI may format and plot emitted values. It must not calculate candidate scores, desired/bounded weights, returns, costs, or intervention status.

**Step 4: Verify desktop and mobile**

```bash
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

Verify 1366x768 and 390x844, keyboard operation, modal focus, SSE reconnect, and zero console errors.

**Step 5: Commit**

```bash
git add frontend
git commit -m "feat: add AgentTrader experiment builder"
```

## Task 13: Start Automatic Forward Paper Experiments

**Files:**
- Create: `../AgentTrader/src/jobs/scheduler.ts`
- Create: `../AgentTrader/src/jobs/forward.ts`
- Create: `../AgentTrader/tests/forward-job.test.ts`
- Modify: `../AgentTrader/src/api/routes/experiments.ts`
- Modify: `frontend/components/ExperimentBuilder.tsx`

**Step 1: Write clock-controlled failing tests**

Test monthly due-date calculation, one decision per scheduled close, retry without duplicate proposal/fill, missing bar deferral, process restart, and automatic execution within the frozen mandate.

**Step 2: Implement a local durable scheduler**

Store next due time in SQLite and poll with an injected clock. Claim jobs transactionally. The scheduler invokes the same candidate -> Pi proposal -> RetailTrader agent-step path as hindsight; only the clock and temporal classification differ.

**Step 3: Use forward-safe information**

Real adjusted prices and SEC facts retrieved/filed by the actual decision time are allowed. Never reuse synthetic hindsight observations in the forward portfolio. If required evidence coverage is insufficient, the agent records abstention rather than weakening the philosophy silently.

**Step 4: Preserve Flue migration**

Keep scheduling behind a `JobScheduler` interface. Flue adoption replaces job claiming/worker launch, not experiment APIs, Pi worker protocol, or RetailTrader artifacts.

**Step 5: Verify and commit**

```bash
cd ../AgentTrader
npm test -- forward-job
git add src tests
git commit -m "feat: schedule automatic forward paper decisions"
```

## Task 14: Complete Acceptance And Documentation

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/architecture.md`
- Modify: `docs/data-integrity.md`
- Modify: `docs/experiment-lifecycle.md`
- Modify: `docs/demo-integrity.md`
- Modify: `../AgentTrader/AGENTS.md`
- Create: `../AgentTrader/README.md`
- Create: `../AgentTrader/docs/artifacts.md`
- Create: `../AgentTrader/docs/flue-integration.md`

**Step 1: Document the trust boundary**

State exactly what Pi decides, what RetailTrader constrains/calculates, what is synthetic, what is live, and why hindsight is not a track record.

**Step 2: Run offline acceptance**

```bash
cd RetailTrader
uv run ruff check .
uv run pytest -q

cd ../AgentTrader
npm ci
npm test
npm run typecheck
npm run build

cd ../RetailTrader/frontend
npm ci
npm run build
npm run test:e2e
```

**Step 3: Run opt-in live smoke**

```bash
RETAILTRADER_LIVE_DATA=1 RETAILTRADER_LIVE_SEC=1 \
  uv run pytest tests/integration/data -v
```

Expected: provider normalization/cache tests pass or skip with a clear credential/network reason; no default test requires network.

**Step 4: Run the product smoke**

Start RetailTrader and AgentTrader, then create the Buffett-inspired experiment through the browser. Confirm:

- a valid philosophy and mandate are frozen;
- approximately 12 candidates are screened from 30;
- Pi emits structured proposals;
- RetailTrader caps at least one deliberately over-limit fixture proposal;
- the hindsight result is visibly classified;
- the forward experiment is scheduled;
- every displayed number traces to an artifact;
- no real order path exists.

**Step 5: Review and commit docs separately in each repository**

Use concise repository-style commit messages. Do not combine generated runs, caches, credentials, Pi sessions, SQLite databases, or frontend output into commits.

## Migration Signals

Require Flue when at least one is true:

- multiple experiment jobs must run concurrently;
- jobs need remote workers or container isolation;
- cancellation/retry ownership no longer fits one AgentTrader process;
- scheduled forward portfolios must survive service redeploys independently;
- operations require a queue/status API shared with other agent products.

Broaden beyond 30 stocks when the symbol catalog, liquidity screen, provider coverage, and point-in-time evidence completeness can be measured and exposed before model calls. Never equate “provider accepts a ticker” with “the experiment has trustworthy data.”
