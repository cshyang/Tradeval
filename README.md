# RetailTrader — Trading Philosophy Lab

A deterministic lab for testing investment philosophies as versioned,
falsifiable specs. Philosophies are YAML factor definitions; a deterministic
engine replays them over the same historical window into isolated paper
portfolios; a static frontend compares the results.

Research prototype. Synthetic/demo data. Not financial advice.

- Plan: `docs/plans/2026-07-16-trading-philosophy-lab.md`
- Rules: `AGENTS.md`

## Quickstart

```bash
uv sync
uv run pytest -q
```

## Run the demo

The engine produces the artifacts; the frontend only renders them.

```bash
uv run retailtrader demo --workspace runs/demo          # replay 3 philosophies (~7s)
uv run retailtrader export --workspace runs/demo        # → frontend/public/runs/
cd frontend && npm install && npm run build && npx serve out
```

`runs/` and `frontend/public/runs/` are generated — rerun the two commands above
after any engine change.

UI design source: claude.ai/design project "Trading Philosophy Lab Demo"
(`Philosophy Lab v2.dc.html`), ported to React in `frontend/app/page.tsx`.
