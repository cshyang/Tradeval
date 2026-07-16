# Demo Integrity Handoff

## Current State

The v3 Philosophy Lab is an offline, deterministic, synthetic demonstration.
The engine, lifecycle CLI, atomic export, and static frontend share versioned
artifacts. Turnover limits are enforced, replay and paper stepping are the same
transition, and resume rejects identity or materialization drift before writes.

The canonical acceptance recipe is in `README.md` and `AGENTS.md`. This tracked
document is the durable handoff; ignored `.remember/` notes are not authoritative.

## Product Claims

- Values shown in the UI originate in engine/export artifacts.
- Philosophy YAML shown beside a result is the exact hashed input for that run.
- The benchmark is explicitly synthetic and never represented as SPY.
- Historical replay is descriptive and synthetic, not predictive.
- No broker or real-money path exists.

## Frontend

The React implementation follows `Philosophy Lab v3.dc.html`. It supports
desktop and stacked mobile layouts, light/dark themes, keyboard-selectable
philosophies and rebalance markers, spec/fork plating, explicit load failures,
and static export through Next.js 15.5.20.

## Deferred

- Live OpenBB/provider/cache integration and real point-in-time filings.
- Pi-generated read-only experiment narration.
- Bull/base/bear scenarios and any future-facing hypothesis engine.
- Functional browser-side philosophy execution; the static UI remains read-only.
- Flue scheduling, broker mirroring, and real-money trading.
- Corporate actions, exchange calendars, liquidity, sentiment, shorting, and leverage.

These are roadmap items, not partially shipped features. Locked UI affordances
must remain visibly disabled until their engine contracts exist.

## Dependency Note

Next.js was upgraded from 15.5.4 to 15.5.20 to remove critical and high
advisories. `npm audit --omit=dev --audit-level=high` passes; npm still reports
moderate transitive PostCSS advisories without a non-breaking Next.js 15 fix.
