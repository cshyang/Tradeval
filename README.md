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
