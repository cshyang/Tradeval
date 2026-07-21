# Demo Integrity Closure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the synthetic demo's simulation, artifact, CLI, frontend, documentation, dependency, and cleanup gaps without expanding into live data or broker features.

**Architecture:** Keep the deterministic Python engine as the sole source of financial values. Build all CLI commands on shared lifecycle functions, make resume and export validation fail before writes, and feed the static Next.js frontend a fully computed view model.

**Tech Stack:** Python 3.12, Pydantic 2, Typer, pytest, Ruff, Next.js 15, React 19, TypeScript, Playwright browser verification.

---

### Task 1: Harden Immutable Contracts

**Files:**
- Modify: `src/retailtrader/domain.py`
- Modify: `tests/unit/test_domain.py`
- Modify: `tests/unit/test_philosophy.py`

**Steps:**
1. Add failing tests that reject `max_turnover` outside `[0, 1]` and accept `None`, `0`, `0.5`, and `1`.
2. Add manifest fields for `data_source`, `benchmark_source`, `initial_cash`, and `slippage_bps`; validate non-empty sources, positive cash, and non-negative slippage.
3. Run the focused tests and confirm the new assertions fail.
4. Implement the minimal validators and defaults required by existing fixtures.
5. Run `uv run pytest tests/unit/test_domain.py tests/unit/test_philosophy.py -q`.

### Task 2: Enforce Turnover In The Shared Execution Path

**Files:**
- Modify: `src/retailtrader/simulation/execution.py`
- Modify: `src/retailtrader/simulation/runner.py`
- Modify: `src/retailtrader/allocation.py`
- Modify: `tests/unit/simulation/test_execution.py`
- Modify: `tests/integration/test_replay_parity.py`

**Steps:**
1. Add hand-calculated failing tests for capped rotation, zero cap, no cap, slippage, deterministic symbol order, rejection records, and non-negative cash.
2. Compute opening equity and unconstrained integer share deltas. If gross slippage-adjusted fill notional exceeds `2 * equity * max_turnover`, scale each absolute delta by the same ratio and floor shares deterministically.
3. Emit `RejectedOrder(reason="max turnover")` for omitted quantities, then use the existing sells-first and affordability path.
4. Pass the validated philosophy limit through `ExperimentRunner` and its shared `step` function.
5. Run execution tests, then replay/forward parity with an active cap.

### Task 3: Validate Resume And Session Order

**Files:**
- Modify: `src/retailtrader/simulation/runner.py`
- Modify: `src/retailtrader/storage/artifacts.py`
- Modify: `tests/integration/test_replay_parity.py`

**Steps:**
1. Add failing tests for changed immutable manifest fields, philosophy text, initial cash, mixed event run IDs, missing identity artifacts, malformed manifests, and unchanged bytes after rejection.
2. Add a dedicated resume error and validate persisted identity artifacts before any write.
3. Ignore incoming `created_at` on valid resume but adopt the persisted manifest as canonical.
4. Restore initial state from the persisted `portfolio_created` event instead of incoming values.
5. Require completed sessions to be a chronological prefix and expose the next expected session to lifecycle callers.
6. Run integration tests and prove valid process-restart parity remains byte-identical.

### Task 4: Repair Point-In-Time Execution Timing

**Files:**
- Modify: `src/retailtrader/data/synthetic.py`
- Modify: `src/retailtrader/cli.py`
- Modify: `tests/unit/data/test_synthetic.py`
- Modify: `tests/unit/test_factors.py`

**Steps:**
1. Add a failing test proving execution-session signals cannot observe fundamentals accepted after that session's open or the execution bar's close.
2. Build each target from a decision snapshot capped at the prior completed session while supplying the incoming session bar only to execution and marking.
3. Preserve the one shared transition path for replay and paper step.
4. Run synthetic-data and factor tests, then parity tests.

### Task 5: Restore Artifact Provenance And Honest Benchmark Names

**Files:**
- Modify: `src/retailtrader/evaluation/report.py`
- Modify: `src/retailtrader/evaluation/metrics.py`
- Modify: `scripts/gen_fixtures.py`
- Modify: `tests/unit/evaluation/test_report.py`
- Modify: `tests/unit/evaluation/test_benchmark_metrics.py`
- Modify: `tests/fixtures/demo-run/*/manifest.json`
- Modify: `tests/fixtures/demo-run/*/evaluation.json`

**Steps:**
1. Change tests to require schema and engine versions in `evaluation.json`.
2. Add source identities and execution settings to fixture manifests.
3. Rename SPY-facing labels and relative metric keys to synthetic mega-cap proxy names across engine artifacts and reports.
4. Update the fixture generator and regenerate frozen fixtures deterministically.
5. Run all evaluation and fixture-contract tests.

### Task 6: Implement The Deterministic Lifecycle CLI

**Files:**
- Refactor: `src/retailtrader/cli.py`
- Create: `tests/unit/test_cli.py`
- Create: `tests/e2e/test_demo.py`

**Steps:**
1. Add failing Typer tests for help, validation, JSON envelopes, stable exit classes, safe run IDs, explicit workspaces, and missing/corrupt runs.
2. Implement ordinary reusable functions for create, load, replay, step, evaluate, compare, and demo; do not add service/repository abstractions.
3. Add command groups for `experiment` and `paper`.
4. Make `paper step` accept an explicit synthetic session, allow an idempotent repeat, and reject skipped or unscheduled sessions.
5. Make replay resume only the chronological suffix and evaluation derive `as_of` from the final artifact session.
6. Make demo call the same lifecycle functions and verify ledger reconstruction for all three runs.
7. Run CLI tests and the offline end-to-end demo test.

### Task 7: Make Export Atomic And Frontend Data Display-Only

**Files:**
- Modify: `src/retailtrader/cli.py`
- Create: `tests/unit/test_export.py`
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/app/page.tsx`

**Steps:**
1. Add failing tests for complete sorted output, malformed artifacts, mixed axes, unsafe path overlap, destination preservation on failure, stale-run removal, and byte-identical reruns.
2. Validate and parse every run before creating output.
3. Build export in a sibling temporary directory and atomically replace the destination.
4. Emit per-rebalance proxy-relative return from Python and remove the financial calculation from React.
5. Replace all SPY labels with `Synthetic mega-cap proxy` and keep raw artifact field compatibility only where necessary.
6. Add explicit loading-error and empty-run states plus keyboard-accessible philosophy controls.
7. Run export tests and TypeScript build.

### Task 8: Patch Frontend Dependencies And Add Browser Acceptance

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/app/page.tsx`
- Modify: `frontend/app/globals.css`
- Add only if needed: minimal browser test configuration and one acceptance test

**Steps:**
1. Resolve current Next.js documentation and identify the patched release compatible with React 19 and static export.
2. Upgrade within the existing major line and run `npm audit --omit=dev`; require no high or critical finding.
3. Declare and enforce a desktop minimum viewport or make the v3 shell responsive; do not leave support undefined.
4. Verify artifact load, philosophy switching, rebalance navigation, comparison, both modals, theme toggle, keyboard access, load failure, and zero console errors in a real browser.
5. Run `npm run build` and the reproducible browser acceptance command.

### Task 9: Complete Durable Documentation And Repository Hygiene

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Create: `docs/architecture.md`
- Create: `docs/data-integrity.md`
- Create: `docs/experiment-lifecycle.md`
- Create: `docs/demo-integrity.md`
- Modify: `.gitignore`
- Remove from Git: `frontend/tsconfig.tsbuildinfo`

**Steps:**
1. Replace stale Phase 1 and v2 language with the current v3 contracts and acceptance commands.
2. Document synthetic-only data, the exact proxy benchmark, point-in-time timing, turnover semantics, artifact provenance, lifecycle commands, generated paths, cleanup, and deferred roadmap.
3. Treat `docs/demo-integrity.md` as the tracked handoff; never rely on ignored `.remember` files.
4. Ignore `*.tsbuildinfo` and remove the generated file from tracking.
5. Check all documentation commands against actual CLI help and behavior.

### Task 10: Regenerate And Verify End To End

**Files:**
- Generated only: `runs/acceptance/`, `frontend/public/runs/`, `frontend/out/`

**Steps:**
1. Run `uv sync --frozen`.
2. Run `uv run ruff check .` and `uv run pytest -q`.
3. Remove only generated acceptance directories, then run the full demo and export commands.
4. Run `npm --prefix frontend ci`, dependency audit, production build, and browser acceptance.
5. Confirm regenerated trend artifacts contain turnover interventions and never exceed the declared cap.
6. Confirm Git shows only intended source/documentation changes and no generated output.
7. Review the complete diff for accidental API drift or weakened invariants.

### Task 11: Integrate And Clean Historical Worktrees

**Steps:**
1. Present the verified closure diff for integration; do not commit or merge without explicit user approval.
2. After integration, remove the two clean historical worktrees with `git worktree remove`.
3. Delete their already-merged branches with `git branch -d` and run `git worktree prune`.
4. Verify both historical commits remain ancestors of `main` and the repository worktree is clean.
