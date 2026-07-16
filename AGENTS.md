# RetailTrader — Agent Rules

Non-negotiable:

- No real-money trading, broker integration, or LLM-generated orders.
- No historical observation without an availability timestamp.
- Replay and paper stepping use `simulation.runner.step`; never create a second path.
- The Python engine is the only source of scores, weights, financial metrics,
  orders, fills, cash, and positions. Frontend calculations are presentational only.
- Never call the synthetic five-stock benchmark SPY. Its user-facing name is
  **Synthetic mega-cap proxy**.
- Manifest, philosophy, event, and materialized-artifact mismatches must fail
  before writes. Do not add compatibility fallbacks that weaken resume integrity.
- Generated paths are ignored. Update frozen fixtures only with their generator
  and verify the complete contract suite.
- `.remember/` is disposable local state. Durable handoff belongs in
  `docs/demo-integrity.md`.

Acceptance:

```bash
uv run ruff check .
uv run pytest -q
uv run retailtrader demo --workspace runs/acceptance
uv run retailtrader export --workspace runs/acceptance --out frontend/public/runs
npm --prefix frontend audit --omit=dev --audit-level=high
npm --prefix frontend run build
```

Plans of record:

- `docs/plans/2026-07-16-trading-philosophy-lab.md`
- `docs/plans/2026-07-16-demo-integrity-closure-design.md`
- `docs/plans/2026-07-16-demo-integrity-closure.md`
