"""RetailTrader CLI — Phase 2 wiring of engine, simulation, and artifacts.

Demo mode: synthetic deterministic data only (no network, no broker).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

import typer
import yaml

from retailtrader.data import synthetic
from retailtrader.domain import ExperimentManifest, MarketSnapshot, PhilosophySpec
from retailtrader.evaluation.metrics import (
    benchmark_metrics,
    compute_evaluation,
    read_equity_csv,
)
from retailtrader.evaluation.report import (
    write_comparison_md,
    write_evaluation_json,
    write_report_md,
)
from retailtrader.philosophy import load_philosophy
from retailtrader.scoring import generate_target
from retailtrader.simulation.runner import ExperimentRunner
from retailtrader.storage.artifacts import read_jsonl

app = typer.Typer(no_args_is_help=True, help="Deterministic trading philosophy lab.")
philosophy_app = typer.Typer(help="Philosophy spec commands.")
app.add_typer(philosophy_app, name="philosophy")

ROOT = Path(__file__).resolve().parent.parent.parent
UNIVERSE_FILE = ROOT / "config" / "universes" / "us-large-cap-30.yaml"
PHILOSOPHY_DIR = ROOT / "philosophies"
INITIAL_CASH = Decimal("100000")
SLIPPAGE_BPS = 5
SPY_PROXY = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL")

EXPORT_ARTIFACTS = (
    "manifest.json",
    "philosophy.yaml",
    "equity.csv",
    "decisions.jsonl",
    "portfolio.jsonl",
    "evaluation.json",
)


@philosophy_app.command("validate")
def philosophy_validate(path: Path) -> None:
    """Strictly validate a philosophy YAML and print its content hash."""
    try:
        spec = load_philosophy(path)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        typer.echo(f"INVALID: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"OK {spec.name} {spec.version} hash={spec.content_hash}")


def _universe_symbols() -> tuple[str, ...]:
    raw = yaml.safe_load(UNIVERSE_FILE.read_text(encoding="utf-8"))
    return tuple(raw["symbols"])


def _rebalance_sessions(start: date, end: date) -> list[date]:
    return [d for d in synthetic.trading_sessions(start, end) if d.weekday() == 4]


def _benchmarks(
    snapshots: list[MarketSnapshot], symbols: tuple[str, ...]
) -> dict[date, tuple[Decimal, Decimal]]:
    """Equal-weight index benchmarks from snapshot closes, based at INITIAL_CASH.

    ponytail: synthetic data has no real SPY — the "spy" column is an
    equal-weight mega-cap proxy, documented in the demo report disclaimer.
    """
    first = {bar.symbol: bar.close for bar in snapshots[0].bars}

    def index_value(snapshot: MarketSnapshot, members: tuple[str, ...]) -> Decimal:
        closes = {bar.symbol: bar.close for bar in snapshot.bars}
        ratios = [closes[s] / first[s] for s in members if s in closes and s in first]
        level = float(INITIAL_CASH) * float(sum(ratios) / len(ratios))
        return Decimal(f"{level:.2f}")

    return {
        snap.as_of.date(): (index_value(snap, SPY_PROXY), index_value(snap, symbols))
        for snap in snapshots
    }


def _make_generator(spec: PhilosophySpec):
    def generate(manifest: ExperimentManifest, snapshot: MarketSnapshot):
        history = {
            bar.symbol: synthetic.price_history(bar.symbol, snapshot.as_of)
            for bar in snapshot.bars
        }
        return generate_target(spec, snapshot, manifest.run_id, history=history)

    return generate


def _evaluate_run(run_dir: Path, manifest: ExperimentManifest, as_of: datetime):
    events = read_jsonl(run_dir / "events.jsonl")
    rejected = sum(e["event_type"] == "order_rejected" for e in events)
    report = compute_evaluation(
        run_id=manifest.run_id,
        as_of=as_of,
        equity=read_equity_csv(run_dir / "equity.csv"),
        fills=read_jsonl(run_dir / "fills.jsonl"),
        portfolios=read_jsonl(run_dir / "portfolio.jsonl"),
        decisions=read_jsonl(run_dir / "decisions.jsonl"),
        constraint_interventions=rejected,
    )
    write_evaluation_json(report, run_dir / "evaluation.json")
    write_report_md(manifest, report, run_dir / "report.md")
    return report


@app.command()
def demo(
    workspace: Path = typer.Option(Path("runs/demo"), help="Run output directory."),
    start: datetime = typer.Option("2024-01-05"),
    end: datetime = typer.Option("2026-06-26"),
) -> None:
    """Replay all philosophy templates over synthetic data and compare them."""
    symbols = _universe_symbols()
    sessions = _rebalance_sessions(start.date(), end.date())
    typer.echo(f"building {len(sessions)} weekly snapshots for {len(symbols)} symbols…")
    snapshots = [synthetic.snapshot_for(symbols, s) for s in sessions]
    benchmarks = _benchmarks(snapshots, symbols)
    universe_hash = hashlib.sha256(UNIVERSE_FILE.read_bytes()).hexdigest()

    runs = []
    for spec_path in sorted(PHILOSOPHY_DIR.glob("*.yaml")):
        spec = load_philosophy(spec_path)
        exp_id = f"exp-{spec.name}-{spec.version}"
        manifest = ExperimentManifest(
            id=exp_id,
            run_id=exp_id,
            philosophy_name=spec.name,
            philosophy_version=spec.version,
            philosophy_hash=spec.content_hash or "",
            universe_hash=universe_hash,
            cadence=spec.cadence,
            start=sessions[0],
            end=sessions[-1],
            created_at=datetime.now(UTC),
        )
        run_dir = workspace / exp_id
        typer.echo(f"replaying {exp_id} over {len(snapshots)} sessions…")
        runner = ExperimentRunner(
            experiment=manifest,
            run_dir=run_dir,
            generate_target=_make_generator(spec),
            benchmarks=benchmarks,
            philosophy_yaml=spec_path.read_text(encoding="utf-8"),
            initial_cash=INITIAL_CASH,
            slippage_bps=SLIPPAGE_BPS,
        )
        final = runner.replay(snapshots)
        as_of = datetime.combine(sessions[-1], time(20), tzinfo=UTC)
        report = _evaluate_run(run_dir, manifest, as_of)
        runs.append((manifest, report))
        typer.echo(
            f"  final equity {final.total_equity} | "
            f"return {report.metrics.total_return:+.2%} | "
            f"max drawdown {report.metrics.max_drawdown:.2%}"
        )

    write_comparison_md(runs, workspace / "comparison.md")
    typer.echo(f"done: {len(runs)} experiments, comparison at {workspace / 'comparison.md'}")


def _display_selected(row: dict) -> dict:
    """Round engine values for display. The frontend renders, never rounds."""
    return {
        "symbol": row["symbol"],
        "weight": round(row["weight"], 6),
        "score": round(row["score"], 4),
        "factors": [
            {
                "name": f["name"],
                "value": None if f["value"] is None else round(f["value"], 3),
                "contribution": round(f["contribution"], 4),
            }
            for f in row["factors"]
        ],
    }


def _display_rejected(row: dict) -> dict:
    return {
        "symbol": row["symbol"],
        "reason": row["reason"],
        "score": None if row["score"] is None else round(row["score"], 4),
    }


def _view_model(runs: list[tuple[dict, Path]]) -> dict:
    """Aggregate exported artifacts into the frontend's single-fetch view model.

    Every number here is copied from an engine artifact or computed by the
    engine's own metric functions — the frontend renders, it never calculates.
    """
    universe_name = yaml.safe_load(UNIVERSE_FILE.read_text(encoding="utf-8"))["name"]
    _, first_dir = runs[0]
    equity_points = read_equity_csv(first_dir / "equity.csv")
    dates = [p.session.isoformat() for p in equity_points]
    spy = [f"{p.spy_equity:.2f}" for p in equity_points]
    equal_weight = [f"{p.equal_weight_equity:.2f}" for p in equity_points]
    week_of = {d: i for i, d in enumerate(dates)}

    experiments = []
    for manifest, run_dir in runs:
        points = read_equity_csv(run_dir / "equity.csv")
        evaluation = json.loads((run_dir / "evaluation.json").read_text(encoding="utf-8"))
        rebalances = []
        for record in read_jsonl(run_dir / "decisions.jsonl"):
            session = record["as_of"][:10]
            if session not in week_of:
                continue
            rebalances.append(
                {
                    "week": week_of[session],
                    "as_of": session,
                    "selected": [_display_selected(s) for s in record["selected"]],
                    "rejected": [_display_rejected(r) for r in record["rejected"]],
                }
            )
        experiments.append(
            {
                "id": manifest["id"],
                "label": manifest["philosophy_name"],
                "version": manifest["philosophy_version"],
                "start": manifest["start"],
                "end": manifest["end"],
                "engine_version": manifest["engine_version"],
                "content_hash": manifest["philosophy_hash"][:12],
                "universe": universe_name,
                "equity": [f"{p.equity:.2f}" for p in points],
                "rebalances": rebalances,
                "evaluation": {
                    "metrics": evaluation["metrics"],
                    "fidelity": evaluation["fidelity"],
                },
            }
        )

    sessions = [p.session for p in equity_points]
    spy_values = [p.spy_equity for p in equity_points]
    ew_values = [p.equal_weight_equity for p in equity_points]
    return {
        "dates": dates,
        "spy": spy,
        "equal_weight": equal_weight,
        "experiments": experiments,
        "benchmarks": {
            "spy": benchmark_metrics(
                values=spy_values,
                sessions=sessions,
                spy_values=spy_values,
                equal_weight_values=ew_values,
            ),
            "equal_weight": benchmark_metrics(
                values=ew_values,
                sessions=sessions,
                spy_values=spy_values,
                equal_weight_values=ew_values,
            ),
        },
    }


@app.command()
def export(
    workspace: Path = typer.Option(Path("runs/demo")),
    out: Path = typer.Option(Path("frontend/public/runs")),
) -> None:
    """Copy run artifacts and the aggregated view model to the frontend."""
    experiments = []
    runs: list[tuple[dict, Path]] = []
    for manifest_path in sorted(workspace.glob("*/manifest.json")):
        run_dir = manifest_path.parent
        missing = [n for n in EXPORT_ARTIFACTS if not (run_dir / n).exists()]
        if missing:
            typer.echo(f"ERROR: {run_dir.name} missing {missing}", err=True)
            raise typer.Exit(code=1)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        dest = out / run_dir.name
        dest.mkdir(parents=True, exist_ok=True)
        for name in EXPORT_ARTIFACTS:
            shutil.copy2(run_dir / name, dest / name)
        runs.append((manifest, run_dir))
        experiments.append(
            {
                "id": manifest["id"],
                "philosophy": manifest["philosophy_name"],
                "version": manifest["philosophy_version"],
                "start": manifest["start"],
                "end": manifest["end"],
            }
        )
    if not experiments:
        typer.echo(f"ERROR: no runs found in {workspace}", err=True)
        raise typer.Exit(code=1)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_text(json.dumps({"experiments": experiments}, indent=2))
    (out / "data.json").write_text(json.dumps(_view_model(runs), separators=(",", ":")))
    size_mb = (out / "data.json").stat().st_size / 1_000_000
    typer.echo(f"exported {len(experiments)} experiments to {out} (data.json {size_mb:.1f} MB)")


if __name__ == "__main__":
    app()
