"""Generate the deterministic demo-run fixture artifacts.

Output under tests/fixtures/demo-run/ is the authoritative copy of the
frontend artifact contract. Run once and commit the output:

    uv run python scripts/gen_fixtures.py
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "demo-run"

UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "LLY", "JPM", "V",
    "JNJ", "WMT", "PG", "UNH", "XOM", "HD", "MA", "COST", "ABBV", "CVX",
    "MRK", "PEP", "KO", "ADBE", "CRM", "NFLX", "AMD", "INTC", "ORCL", "CSCO",
]

FACTORS = {
    "trend": ["momentum_6m", "momentum_12m", "above_sma_200", "volatility_60d"],
    "quality-value": ["roic", "fcf_yield", "debt_to_ebitda", "free_cash_flow_consistency"],
    "garp": ["revenue_growth_3y", "eps_growth_3y", "growth_adjusted_pe", "debt_to_ebitda"],
}

# ponytail: narrative-tuned drifts so the demo has a story
# (trend wins overall, quality-value wins the drawdown, garp in between)
PROFILES = {
    "trend": (0.0036, 0.021, 0.032),
    "quality-value": (0.0023, 0.012, 0.013),
    "garp": (0.0029, 0.016, 0.024),
}

BASE = 100_000.0
START = date(2024, 1, 5)
WEEKS = 130
SHOCK_START, SHOCK_LEN = 78, 6  # mid-2025 drawdown for drama


def weekly_dates() -> list[date]:
    return [START + timedelta(weeks=i) for i in range(WEEKS)]


def equity_curve(rng: np.random.Generator, mu: float, sigma: float, shock: float) -> np.ndarray:
    returns = rng.normal(mu, sigma, WEEKS - 1)
    returns[SHOCK_START : SHOCK_START + SHOCK_LEN] -= shock / SHOCK_LEN
    curve = BASE * np.cumprod(np.concatenate([[1.0], 1 + returns]))
    return curve


def metrics_from(curve: np.ndarray, bench: np.ndarray, ew: np.ndarray, rng: np.random.Generator) -> dict:
    rets = np.diff(curve) / curve[:-1]
    total = float(curve[-1] / curve[0] - 1)
    years = (WEEKS - 1) / 52
    peak = np.maximum.accumulate(curve)
    return {
        "metrics": {
            "total_return": round(total, 4),
            "cagr": round(float((curve[-1] / curve[0]) ** (1 / years) - 1), 4),
            "volatility": round(float(np.std(rets, ddof=1) * np.sqrt(52)), 4),
            "sharpe": round(float(np.mean(rets) / np.std(rets, ddof=1) * np.sqrt(52)), 2),
            "max_drawdown": round(float(((curve - peak) / peak).min()), 4),
            "turnover": round(float(rng.uniform(0.10, 0.25)), 4),
            "trade_count": int(rng.integers(180, 320)),
            "avg_holding_days": round(float(rng.uniform(35, 90)), 1),
            "cash_exposure": 0.05,
            "max_concentration": 0.1188,
            "synthetic_mega_cap_proxy_relative": round(
                total - float(bench[-1] / bench[0] - 1), 4
            ),
            "equal_weight_relative": round(total - float(ew[-1] / ew[0] - 1), 4),
        },
        "fidelity": {
            "factor_coverage": round(float(rng.uniform(0.9, 0.99)), 4),
            "constraint_interventions": int(rng.integers(2, 14)),
            "ranking_churn": round(float(rng.uniform(0.1, 0.3)), 4),
            "selection_stability": round(float(rng.uniform(0.7, 0.92)), 4),
            "rule_violations": 0,
        },
    }


def main() -> None:
    rng = np.random.default_rng(42)
    dates = weekly_dates()
    proxy = equity_curve(rng, 0.0026, 0.015, 0.028)
    ew = equity_curve(rng, 0.0024, 0.014, 0.026)

    index = {"experiments": []}
    for name, (mu, sigma, shock) in PROFILES.items():
        exp_id = f"exp-{name}-v1-2024"
        exp_dir = OUT / exp_id
        exp_dir.mkdir(parents=True, exist_ok=True)
        curve = equity_curve(rng, mu, sigma, shock)

        index["experiments"].append(
            {
                "id": exp_id,
                "philosophy": name,
                "version": "v1",
                "start": dates[0].isoformat(),
                "end": dates[-1].isoformat(),
            }
        )

        (exp_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "id": exp_id,
                    "run_id": exp_id,
                    "schema_version": 1,
                    "philosophy_name": name,
                    "philosophy_version": "v1",
                    "philosophy_hash": f"fixture-{name}-hash",
                    "universe_hash": "fixture-us-large-cap-30",
                    "engine_version": "0.1.0",
                    "cadence": "weekly",
                    "start": dates[0].isoformat(),
                    "end": dates[-1].isoformat(),
                    "created_at": "2026-07-16T00:00:00+00:00",
                    "data_source": "synthetic-v1",
                    "benchmark_source": "synthetic-mega-cap-proxy-v1",
                    "initial_cash": "100000.00",
                    "slippage_bps": 5,
                },
                indent=2,
            )
        )

        rows = ["date,equity,synthetic_mega_cap_proxy_equity,equal_weight_equity"]
        rows += [
            f"{d.isoformat()},{curve[i]:.2f},{proxy[i]:.2f},{ew[i]:.2f}"
            for i, d in enumerate(dates)
        ]
        (exp_dir / "equity.csv").write_text("\n".join(rows) + "\n")

        decisions, portfolio = [], []
        for i, d in enumerate(dates):
            as_of = f"{d.isoformat()}T20:00:00+00:00"
            picks = sorted(rng.choice(UNIVERSE, size=8, replace=False).tolist())
            scores = np.sort(rng.uniform(0.62, 0.94, 8))[::-1]
            selected = []
            for sym, score in zip(picks, scores):
                contribs = rng.dirichlet(np.ones(4)) * score
                selected.append(
                    {
                        "symbol": sym,
                        "weight": 0.1188,
                        "score": round(float(score), 4),
                        "factors": [
                            {
                                "name": f,
                                "value": round(float(rng.uniform(-1.5, 2.5)), 3),
                                "contribution": round(float(c), 4),
                            }
                            for f, c in zip(FACTORS[name], contribs)
                        ],
                    }
                )
            rejected = [
                {
                    "symbol": sym,
                    "reason": str(
                        rng.choice(["score below cutoff", "insufficient factor coverage"])
                    ),
                    "score": round(float(rng.uniform(0.2, 0.6)), 4),
                }
                for sym in sorted(rng.choice(
                    [s for s in UNIVERSE if s not in picks], size=4, replace=False
                ).tolist())
            ]
            decisions.append(
                {"as_of": as_of, "selected": selected, "rejected": rejected}
            )

            equity = float(curve[i])
            cash = equity * 0.05
            positions = []
            for sel in selected:
                value = equity * 0.95 / 8
                price = float(rng.uniform(60, 550))
                positions.append(
                    {
                        "symbol": sel["symbol"],
                        "quantity": int(value / price),
                        "price": f"{price:.2f}",
                        "value": f"{value:.2f}",
                    }
                )
            portfolio.append(
                {
                    "as_of": as_of,
                    "cash": f"{cash:.2f}",
                    "positions": positions,
                    "total_equity": f"{equity:.2f}",
                }
            )

        (exp_dir / "decisions.jsonl").write_text(
            "\n".join(json.dumps(x) for x in decisions) + "\n"
        )
        (exp_dir / "portfolio.jsonl").write_text(
            "\n".join(json.dumps(x) for x in portfolio) + "\n"
        )
        (exp_dir / "evaluation.json").write_text(
            json.dumps(
                {
                    "run_id": exp_id,
                    "as_of": f"{dates[-1].isoformat()}T20:00:00+00:00",
                    "schema_version": 1,
                    "engine_version": "0.1.0",
                }
                | metrics_from(curve, proxy, ew, rng),
                indent=2,
            )
        )

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.json").write_text(json.dumps(index, indent=2))
    print(f"wrote fixtures for {len(index['experiments'])} experiments to {OUT}")


if __name__ == "__main__":
    main()
