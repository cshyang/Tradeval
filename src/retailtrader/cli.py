"""RetailTrader CLI — Phase 2 wiring of engine, simulation, and artifacts.

Demo mode: synthetic deterministic data only (no network, no broker).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import typer
import yaml

from retailtrader.data import synthetic
from retailtrader.data.cache import (
    CachedDailyPriceSource,
    DailyPriceLoader,
    PriceCache,
)
from retailtrader.data.openbb import OpenBBYFinancePriceSource
from retailtrader.data.protocol import (
    PriceBatch,
    PriceQuery,
    canonical_json,
    query_key,
    validate_batch_identity,
)
from retailtrader.data.replay import (
    REFERENCE_METHOD_VERSION,
    build_price_frames,
    build_reference_indices,
    history_as_of,
)
from retailtrader.domain import (
    ENGINE_VERSION,
    ExperimentManifest,
    MarketSnapshot,
    PhilosophySpec,
)
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
from retailtrader.factors import FUNDAMENTAL_FACTORS
from retailtrader.philosophy import load_philosophy
from retailtrader.scoring import generate_target
from retailtrader.simulation.frame import SimulationFrame
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
BENCHMARK_SYMBOL = "SPY"
WARMUP_DAYS = 400
MIN_TREND_HISTORY = 253
EXECUTION_MODEL_VERSION = "prior_close_next_open_v1"
PROVENANCE_SCHEMA_VERSION = 1

EXPORT_ARTIFACTS = (
    "manifest.json",
    "philosophy.yaml",
    "equity.csv",
    "decisions.jsonl",
    "portfolio.jsonl",
    "evaluation.json",
    "data-provenance.json",
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


def _simulation_frames(
    symbols: tuple[str, ...], decision_sessions: list[date]
) -> list[SimulationFrame]:
    """Pair each decision session with the immediately following session."""
    market_tz = ZoneInfo("America/New_York")
    frames = []
    for decision_session in decision_sessions:
        execution_sessions = synthetic.trading_sessions(
            decision_session + timedelta(days=1),
            decision_session + timedelta(days=7),
        )
        execution_session = execution_sessions[0]
        execution_at = datetime.combine(
            execution_session, time(9, 30), tzinfo=market_tz
        ).astimezone(UTC)
        frames.append(
            SimulationFrame(
                decision=synthetic.snapshot_for(symbols, decision_session),
                execution=synthetic.snapshot_for(symbols, execution_session),
                execution_at=execution_at,
            )
        )
    return frames


def _benchmarks(
    snapshots: list[MarketSnapshot], symbols: tuple[str, ...]
) -> dict[date, tuple[Decimal, Decimal]]:
    """Equal-weight references funded at the first execution open and marked close.

    ponytail: synthetic data has no real SPY — the "spy" column is an
    equal-weight mega-cap proxy, documented in the demo report disclaimer.
    """
    first_opens = {bar.symbol: bar.open for bar in snapshots[0].bars}

    def index_value(snapshot: MarketSnapshot, members: tuple[str, ...]) -> Decimal:
        closes = {bar.symbol: bar.close for bar in snapshot.bars}
        ratios = [
            closes[s] / first_opens[s]
            for s in members
            if s in closes and s in first_opens
        ]
        level = float(INITIAL_CASH) * float(sum(ratios) / len(ratios))
        return Decimal(f"{level:.2f}")

    return {
        snap.as_of.date(): (index_value(snap, SPY_PROXY), index_value(snap, symbols))
        for snap in snapshots
    }


HistoryLookup = Callable[[MarketSnapshot], Mapping[str, tuple[Any, ...]]]


def _make_generator(spec: PhilosophySpec, history_lookup: HistoryLookup | None = None):
    def generate(manifest: ExperimentManifest, snapshot: MarketSnapshot):
        if history_lookup is None:
            history = {
                bar.symbol: synthetic.price_history(bar.symbol, snapshot.as_of)
                for bar in snapshot.bars
            }
        else:
            history = history_lookup(snapshot)
        return generate_target(spec, snapshot, manifest.run_id, history=history)

    return generate


def _identity_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(payload)).encode("utf-8")).hexdigest()


def _synthetic_provenance(
    manifest: ExperimentManifest, frames: list[SimulationFrame]
) -> dict[str, Any]:
    identity = {
        "identity_version": 1,
        "kind": "synthetic",
        "validity": "synthetic_demo",
        "start": frames[0].decision.as_of.date(),
        "end": frames[-1].execution.as_of.date(),
        "philosophy_hash": manifest.philosophy_hash,
        "universe_hash": manifest.universe_hash,
        "engine_version": manifest.engine_version,
        "initial_cash": str(INITIAL_CASH),
        "slippage_bps": SLIPPAGE_BPS,
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "reference_method_version": REFERENCE_METHOD_VERSION,
    }
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "kind": "synthetic",
        "validity": "synthetic_demo",
        "label": "SYNTHETIC DEMO DATA",
        "transport": "generated",
        "provider": "synthetic",
        "provider_versions": [],
        "adjustment": "none",
        "benchmark_kind": "no_cost_reference",
        "reference_method_version": REFERENCE_METHOD_VERSION,
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "run_identity_hash": _identity_hash(identity),
        "warnings": [
            "Generated deterministic prices and fundamentals; not observed market data.",
            "The SPY display series is a synthetic mega-cap proxy.",
        ],
    }


def _evaluate_run(
    run_dir: Path,
    manifest: ExperimentManifest,
    as_of: datetime,
    data_provenance: Mapping[str, Any] | None = None,
):
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
    write_report_md(
        manifest,
        report,
        run_dir / "report.md",
        data_provenance=data_provenance,
    )
    return report


@app.command()
def demo(
    workspace: Path = typer.Option(Path("runs/demo"), help="Run output directory."),
    start: datetime = typer.Option("2024-01-05"),
    end: datetime = typer.Option("2026-06-26"),
) -> None:
    """Replay all philosophy templates over synthetic data and compare them."""
    symbols = _universe_symbols()
    decision_sessions = _rebalance_sessions(start.date(), end.date())
    typer.echo(
        f"building {len(decision_sessions)} weekly frames for {len(symbols)} symbols…"
    )
    frames = _simulation_frames(symbols, decision_sessions)
    if len(frames) < 3:
        raise typer.BadParameter(
            f"evaluation requires at least 3 simulation frames; got {len(frames)}"
        )
    execution_snapshots = [frame.execution for frame in frames]
    benchmarks = _benchmarks(execution_snapshots, symbols)
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
            start=frames[0].decision.as_of.date(),
            end=frames[-1].execution.as_of.date(),
            created_at=datetime.now(UTC),
        )
        run_dir = workspace / exp_id
        provenance = _synthetic_provenance(manifest, frames)
        typer.echo(f"replaying {exp_id} over {len(frames)} frames…")
        runner = ExperimentRunner(
            experiment=manifest,
            run_dir=run_dir,
            generate_target=_make_generator(spec),
            benchmarks=benchmarks,
            philosophy_yaml=spec_path.read_text(encoding="utf-8"),
            initial_cash=INITIAL_CASH,
            slippage_bps=SLIPPAGE_BPS,
            data_provenance=provenance,
        )
        final = runner.replay(frames)
        report = _evaluate_run(
            run_dir,
            manifest,
            frames[-1].execution.as_of,
            provenance,
        )
        runs.append((manifest, report))
        typer.echo(
            f"  final equity {final.total_equity} | "
            f"return {report.metrics.total_return:+.2%} | "
            f"max drawdown {report.metrics.max_drawdown:.2%}"
        )

    write_comparison_md(runs, workspace / "comparison.md")
    typer.echo(f"done: {len(runs)} experiments, comparison at {workspace / 'comparison.md'}")


def _require_price_only(spec: PhilosophySpec) -> None:
    metrics = {factor.name for factor in spec.factors} | {
        filter_.metric for filter_ in spec.filters
    }
    unsupported = sorted(metrics & set(FUNDAMENTAL_FACTORS))
    if unsupported:
        raise ValueError(
            "real-price v1 supports price factors only; fundamental factors found: "
            + ", ".join(unsupported)
        )


def _market_provenance(
    *,
    batch: PriceBatch,
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "kind": "real_market",
        "validity": "hindsight_current_universe",
        "label": "HINDSIGHT · ADJUSTED MARKET DATA",
        "transport": batch.transport,
        "provider": batch.provider,
        "provider_versions": [list(item) for item in batch.provider_versions],
        "adjustment": batch.query.adjustment,
        "retrieved_at": batch.retrieved_at,
        "query": {
            "symbols": list(batch.query.symbols),
            "start": batch.query.start,
            "end": batch.query.end,
            "interval": batch.query.interval,
            "adjustment": batch.query.adjustment,
        },
        "query_hash": query_key(batch.transport, batch.provider, batch.query),
        "raw_hash": batch.raw_hash,
        "normalized_hash": batch.normalized_hash,
        "source_refs": sorted(
            {observation.source_ref for observation in batch.observations}
        ),
        "benchmark_kind": "no_cost_reference",
        "reference_method_version": REFERENCE_METHOD_VERSION,
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "run_identity": dict(identity),
        "run_identity_hash": _identity_hash(identity),
        "warnings": [
            "The fixed present-day large-cap universe introduces survivorship bias.",
            "Adjusted OHLC fills are normalized research prices, not observed executable quotes.",
            "SPY and equal-weight series are no-cost fractional reference indices.",
            "Yahoo Finance is an unofficial upstream with no availability SLA.",
        ],
    }


def _run_market_replay(
    *,
    loader: DailyPriceLoader,
    workspace: Path,
    start: date,
    end: date,
) -> Path:
    """Run one fake-money trend replay from a validated daily-price loader."""
    if start >= end:
        raise ValueError("market replay start must precede end")
    symbols = _universe_symbols()
    spec_path = PHILOSOPHY_DIR / "trend-v1.yaml"
    spec = load_philosophy(spec_path)
    _require_price_only(spec)
    query = PriceQuery(
        (*symbols, BENCHMARK_SYMBOL),
        start - timedelta(days=WARMUP_DAYS),
        end,
    )
    fetch = loader.fetch(query)
    batch = fetch.batch
    validate_batch_identity(
        batch,
        transport=getattr(loader, "transport", batch.transport),
        provider=getattr(loader, "provider", batch.provider),
        query=query,
    )
    frames = list(build_price_frames(batch, symbols, start, end, BENCHMARK_SYMBOL))
    if len(frames) < 3:
        raise ValueError(
            f"evaluation requires at least 3 real-price frames; got {len(frames)}"
        )
    first_history = history_as_of(batch, symbols, frames[0].decision.as_of)
    insufficient = sorted(
        symbol
        for symbol in symbols
        if len(first_history.get(symbol, ())) < MIN_TREND_HISTORY
    )
    if insufficient:
        raise ValueError(
            f"need {MIN_TREND_HISTORY} completed sessions before the first decision for: "
            + ", ".join(insufficient)
        )

    references = build_reference_indices(
        frames, batch, symbols, INITIAL_CASH, BENCHMARK_SYMBOL
    )
    universe_hash = hashlib.sha256(UNIVERSE_FILE.read_bytes()).hexdigest()
    identity = {
        "identity_version": 1,
        "requested_start": start,
        "requested_end": end,
        "actual_start": frames[0].decision.as_of.date(),
        "actual_end": frames[-1].execution.as_of.date(),
        "transport": batch.transport,
        "provider": batch.provider,
        "provider_versions": batch.provider_versions,
        "adjustment": batch.query.adjustment,
        "query_hash": query_key(batch.transport, batch.provider, batch.query),
        "normalized_hash": batch.normalized_hash,
        "philosophy_hash": spec.content_hash or "",
        "universe_hash": universe_hash,
        "engine_version": ENGINE_VERSION,
        "initial_cash": str(INITIAL_CASH),
        "slippage_bps": SLIPPAGE_BPS,
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "reference_method_version": REFERENCE_METHOD_VERSION,
    }
    identity_hash = _identity_hash(identity)
    run_id = f"exp-{spec.name}-{spec.version}-market-{identity_hash[:16]}"
    manifest = ExperimentManifest(
        id=run_id,
        run_id=run_id,
        philosophy_name=spec.name,
        philosophy_version=spec.version,
        philosophy_hash=spec.content_hash or "",
        universe_hash=universe_hash,
        cadence=spec.cadence,
        start=frames[0].decision.as_of.date(),
        end=frames[-1].execution.as_of.date(),
        created_at=datetime.now(UTC),
    )
    provenance = _market_provenance(batch=batch, identity=identity)
    run_dir = workspace / run_id
    typer.echo(
        f"replaying {run_id} over {len(frames)} frames | "
        f"{batch.transport}/{batch.provider} | {batch.query.adjustment} | "
        f"cache={fetch.cache_status} | validity=hindsight_current_universe"
    )

    def market_history(snapshot: MarketSnapshot):
        return history_as_of(batch, symbols, snapshot.as_of)

    runner = ExperimentRunner(
        experiment=manifest,
        run_dir=run_dir,
        generate_target=_make_generator(spec, market_history),
        benchmarks=references,
        philosophy_yaml=spec_path.read_text(encoding="utf-8"),
        initial_cash=INITIAL_CASH,
        slippage_bps=SLIPPAGE_BPS,
        data_provenance=provenance,
    )
    final = runner.replay(frames)
    report = _evaluate_run(
        run_dir,
        manifest,
        frames[-1].execution.as_of,
        provenance,
    )
    write_comparison_md([(manifest, report)], workspace / "comparison.md")
    typer.echo(
        f"done: final equity {final.total_equity} | "
        f"return {report.metrics.total_return:+.2%} | run {run_dir}"
    )
    return run_dir


@app.command("market-replay")
def market_replay(
    start: datetime = typer.Option(..., help="First execution date (YYYY-MM-DD)."),
    end: datetime = typer.Option(..., help="Last execution date (YYYY-MM-DD)."),
    workspace: Path = typer.Option(Path("runs/market"), help="Run output directory."),
    cache: Path = typer.Option(Path("data/cache"), help="Immutable price cache."),
) -> None:
    """Replay the trend philosophy on real adjusted daily prices and fake cash."""
    loader = CachedDailyPriceSource(
        OpenBBYFinancePriceSource(),
        PriceCache(cache),
    )
    try:
        _run_market_replay(
            loader=loader,
            workspace=workspace,
            start=start.date(),
            end=end.date(),
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc


def _tagline(spec_yaml: str) -> str:
    """The spec file's leading comment block, used as the philosophy's tagline."""
    lines = []
    for line in spec_yaml.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line.lstrip("#").strip())
    return " ".join(lines)


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


_PUBLIC_PROVENANCE_FIELDS = (
    "kind",
    "validity",
    "label",
    "transport",
    "provider",
    "provider_versions",
    "adjustment",
    "retrieved_at",
    "query_hash",
    "normalized_hash",
    "benchmark_kind",
    "reference_method_version",
    "execution_model_version",
    "warnings",
)


def _public_provenance(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "data-provenance.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain an object")
    required = {
        "kind",
        "validity",
        "label",
        "transport",
        "provider",
        "adjustment",
        "benchmark_kind",
        "reference_method_version",
        "execution_model_version",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"{path} missing provenance fields: {missing}")
    return {
        key: payload[key]
        for key in _PUBLIC_PROVENANCE_FIELDS
        if key in payload
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

    experiments = []
    for manifest, run_dir in runs:
        points = read_equity_csv(run_dir / "equity.csv")
        evaluation = json.loads((run_dir / "evaluation.json").read_text(encoding="utf-8"))
        decisions = read_jsonl(run_dir / "decisions.jsonl")
        if len(decisions) != len(points):
            raise ValueError(
                f"{run_dir.name} has {len(decisions)} decisions but "
                f"{len(points)} equity transitions"
            )
        rebalances = []
        for week, (record, point) in enumerate(zip(decisions, points, strict=True)):
            rebalances.append(
                {
                    "week": week,
                    "as_of": record["as_of"][:10],
                    "execution_as_of": point.session.isoformat(),
                    "selected": [_display_selected(s) for s in record["selected"]],
                    "rejected": [_display_rejected(r) for r in record["rejected"]],
                }
            )
        spec_yaml = (run_dir / "philosophy.yaml").read_text(encoding="utf-8")
        experiments.append(
            {
                "id": manifest["id"],
                "label": manifest["philosophy_name"],
                "philosophy": manifest["philosophy_name"],
                "version": manifest["philosophy_version"],
                "start": manifest["start"],
                "end": manifest["end"],
                "cadence": manifest["cadence"],
                "engine_version": manifest["engine_version"],
                "content_hash": manifest["philosophy_hash"][:12],
                "universe": universe_name,
                "spec_yaml": spec_yaml,
                "tagline": _tagline(spec_yaml),
                "data_provenance": _public_provenance(run_dir),
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
