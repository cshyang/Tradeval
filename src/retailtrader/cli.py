"""Deterministic synthetic experiment lifecycle and frontend export CLI."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, NoReturn

import typer
import yaml

from retailtrader.data import synthetic
from retailtrader.data.cache import CachedDailyPriceSource, DailyPriceLoader, PriceCache
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
    market_open_utc,
)
from retailtrader.domain import (
    ENGINE_VERSION,
    EvaluationReport,
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
    evaluation_payload,
    write_comparison_md,
    write_evaluation_json,
    write_report_md,
)
from retailtrader.factors import FUNDAMENTAL_FACTORS
from retailtrader.philosophy import load_philosophy
from retailtrader.scoring import generate_target
from retailtrader.simulation.frame import SimulationFrame
from retailtrader.simulation.ledger import replay_events
from retailtrader.simulation.runner import (
    ExperimentRunner,
    ResumeMismatchError,
    remaining_session_suffix,
)
from retailtrader.storage.artifacts import (
    EQUITY_HEADER,
    SPY_EQUITY_HEADER,
    read_jsonl,
    read_manifest,
)
from retailtrader.storage.events import to_jsonable

app = typer.Typer(no_args_is_help=True, help="Deterministic trading philosophy lab.")
philosophy_app = typer.Typer(help="Validate versioned philosophy specifications.")
experiment_app = typer.Typer(help="Create, replay, evaluate, and compare experiments.")
paper_app = typer.Typer(help="Advance a synthetic paper experiment one session.")
app.add_typer(philosophy_app, name="philosophy")
app.add_typer(experiment_app, name="experiment")
app.add_typer(paper_app, name="paper")

ROOT = Path(__file__).resolve().parent.parent.parent
UNIVERSE_FILE = ROOT / "config" / "universes" / "us-large-cap-30.yaml"
PHILOSOPHY_DIR = ROOT / "philosophies"
INITIAL_CASH = Decimal("100000")
SLIPPAGE_BPS = 5
DATA_SOURCE = "synthetic-v1"
BENCHMARK_SOURCE = "synthetic-mega-cap-proxy-v1"
SYNTHETIC_MEGA_CAP_PROXY = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL")
REAL_DATA_SOURCE = "openbb-yfinance-adjusted-v1"
REAL_BENCHMARK_SOURCE = "spy-no-cost-reference-v1"
BENCHMARK_SYMBOL = "SPY"
WARMUP_DAYS = 400
CALENDAR_BUFFER_DAYS = 7
MIN_TREND_HISTORY = 253
EXECUTION_MODEL_VERSION = "prior_close_next_open_v1"
PROVENANCE_SCHEMA_VERSION = 1
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")

EXPORT_ARTIFACTS = (
    "manifest.json",
    "philosophy.yaml",
    "equity.csv",
    "decisions.jsonl",
    "portfolio.jsonl",
    "evaluation.json",
    "data-provenance.json",
)


class OutputFormat(str, Enum):
    text = "text"
    json = "json"


class CliError(ValueError):
    def __init__(self, code: str, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


def _emit(command: str, result: dict[str, Any], output_format: OutputFormat) -> None:
    if output_format is OutputFormat.json:
        payload = {
            "schema_version": 1,
            "command": command,
            "status": "ok",
            "result": to_jsonable(result),
        }
        typer.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return
    message = result.pop("message", None)
    typer.echo(message or json.dumps(to_jsonable(result), indent=2, sort_keys=True))


def _fail(command: str, error: CliError, output_format: OutputFormat) -> NoReturn:
    if output_format is OutputFormat.json:
        payload = {
            "schema_version": 1,
            "command": command,
            "status": "error",
            "error": {"code": error.code, "message": str(error)},
        }
        typer.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        typer.echo(f"ERROR [{error.code}]: {error}", err=True)
    raise typer.Exit(error.exit_code)


def _execute(
    command: str,
    output_format: OutputFormat,
    action: Callable[[], dict[str, Any]],
) -> None:
    try:
        _emit(command, action(), output_format)
    except CliError as exc:
        _fail(command, exc, output_format)
    except ResumeMismatchError as exc:
        _fail(command, CliError("resume_mismatch", str(exc), 5), output_format)
    except FileNotFoundError as exc:
        _fail(command, CliError("not_found", str(exc), 4), output_format)
    except (ValueError, KeyError, json.JSONDecodeError, yaml.YAMLError) as exc:
        _fail(command, CliError("invalid_input", str(exc), 3), output_format)
    except Exception as exc:  # noqa: BLE001 - stable CLI process boundary
        _fail(command, CliError("internal_error", str(exc), 70), output_format)


def _validate_run_id(run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id) or run_id in {".", ".."}:
        raise CliError("invalid_run_id", f"unsafe run id: {run_id!r}", 3)
    return run_id


def _universe() -> tuple[str, tuple[str, ...], str]:
    raw = yaml.safe_load(UNIVERSE_FILE.read_text(encoding="utf-8"))
    return (
        raw["name"],
        tuple(raw["symbols"]),
        hashlib.sha256(UNIVERSE_FILE.read_bytes()).hexdigest(),
    )


def _scheduled_sessions(start: date, end: date, cadence: str) -> tuple[date, ...]:
    trading = synthetic.trading_sessions(start, end)
    if cadence == "weekly":
        sessions = [session for session in trading if session.weekday() == 4]
    elif cadence == "monthly":
        by_month: dict[tuple[int, int], date] = {}
        for session in trading:
            by_month[(session.year, session.month)] = session
        sessions = list(by_month.values())
    else:
        raise ValueError(f"unsupported cadence: {cadence}")
    if len(sessions) < 3:
        raise ValueError("experiment window must contain at least three rebalance sessions")
    return tuple(sessions)


def _snapshots(symbols: tuple[str, ...], sessions: Sequence[date]) -> list[MarketSnapshot]:
    return [synthetic.snapshot_for(symbols, session) for session in sessions]


def _frames(
    symbols: tuple[str, ...], execution_sessions: Sequence[date]
) -> list[SimulationFrame]:
    return [
        SimulationFrame(
            decision=synthetic.decision_snapshot_for(symbols, session),
            execution=synthetic.snapshot_for(symbols, session),
            execution_at=market_open_utc(session),
        )
        for session in execution_sessions
    ]


def _benchmarks(
    snapshots: Sequence[MarketSnapshot], symbols: tuple[str, ...]
) -> dict[date, tuple[Decimal, Decimal]]:
    first = {bar.symbol: bar.open for bar in snapshots[0].bars}

    def index_value(snapshot: MarketSnapshot, members: tuple[str, ...]) -> Decimal:
        closes = {bar.symbol: bar.close for bar in snapshot.bars}
        ratios = [closes[symbol] / first[symbol] for symbol in members]
        return Decimal(f"{float(INITIAL_CASH) * float(sum(ratios) / len(ratios)):.2f}")

    return {
        snapshot.as_of.date(): (
            index_value(snapshot, SYNTHETIC_MEGA_CAP_PROXY),
            index_value(snapshot, symbols),
        )
        for snapshot in snapshots
    }


HistoryLookup = Callable[[MarketSnapshot], Mapping[str, tuple[Any, ...]]]


def _make_generator(
    spec: PhilosophySpec, history_lookup: HistoryLookup | None = None
):
    def generate(manifest: ExperimentManifest, decision_snapshot: MarketSnapshot):
        symbols = tuple(sorted(bar.symbol for bar in decision_snapshot.bars))
        history = (
            {
                symbol: synthetic.price_history(
                    symbol, decision_snapshot.as_of + timedelta(days=1)
                )
                for symbol in symbols
            }
            if history_lookup is None
            else history_lookup(decision_snapshot)
        )
        return generate_target(
            spec, decision_snapshot, manifest.run_id, history=history
        )

    return generate


def _manifest(
    *, run_id: str, spec: PhilosophySpec, start: date, end: date, universe_hash: str
) -> ExperimentManifest:
    sessions = _scheduled_sessions(start, end, spec.cadence)
    return ExperimentManifest(
        id=run_id,
        run_id=run_id,
        philosophy_name=spec.name,
        philosophy_version=spec.version,
        philosophy_hash=spec.content_hash or "",
        universe_hash=universe_hash,
        cadence=spec.cadence,
        start=sessions[0],
        end=sessions[-1],
        created_at=datetime.now(UTC),
        data_source=DATA_SOURCE,
        benchmark_source=BENCHMARK_SOURCE,
        initial_cash=INITIAL_CASH,
        slippage_bps=SLIPPAGE_BPS,
    )


def _identity_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(payload)).encode("utf-8")).hexdigest()


def _synthetic_provenance(manifest: ExperimentManifest) -> dict[str, Any]:
    identity = {
        "identity_version": 1,
        "kind": "synthetic",
        "validity": "synthetic_demo",
        "start": manifest.start,
        "end": manifest.end,
        "philosophy_hash": manifest.philosophy_hash,
        "universe_hash": manifest.universe_hash,
        "engine_version": manifest.engine_version,
        "initial_cash": str(manifest.initial_cash),
        "slippage_bps": manifest.slippage_bps,
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
            "The Synthetic mega-cap proxy is not SPY.",
        ],
    }


def _run_dir(workspace: Path, run_id: str) -> Path:
    return workspace / _validate_run_id(run_id)


def _create_experiment(
    philosophy_path: Path,
    workspace: Path,
    run_id: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    spec = load_philosophy(philosophy_path)
    universe_name, symbols, universe_hash = _universe()
    if spec.universe != universe_name:
        raise CliError("unknown_universe", f"unknown universe: {spec.universe}", 3)
    run_dir = _run_dir(workspace, run_id)
    if run_dir.exists():
        raise CliError("run_exists", f"run already exists: {run_id}", 5)
    manifest = _manifest(
        run_id=run_id, spec=spec, start=start, end=end, universe_hash=universe_hash
    )
    sessions = _scheduled_sessions(manifest.start, manifest.end, manifest.cadence)
    frames = _frames(symbols, sessions)
    ExperimentRunner(
        experiment=manifest,
        run_dir=run_dir,
        generate_target=_make_generator(spec),
        benchmarks=_benchmarks([frame.execution for frame in frames], symbols),
        philosophy_yaml=philosophy_path.read_text(encoding="utf-8"),
        max_turnover=spec.max_turnover,
        data_provenance=_synthetic_provenance(manifest),
    )
    return {
        "run_id": run_id,
        "manifest": to_jsonable(manifest.model_dump()),
        "message": f"created {run_id} ({manifest.start} to {manifest.end}, {DATA_SOURCE})",
    }


def _load_context(workspace: Path, run_id: str):
    run_dir = _run_dir(workspace, run_id)
    if not run_dir.is_dir():
        raise CliError("run_not_found", f"run not found: {run_id}", 4)
    manifest = read_manifest(run_dir / "manifest.json")
    if manifest.run_id != run_id or manifest.id != run_id:
        raise CliError("run_identity_mismatch", "run directory and manifest disagree", 3)
    spec = load_philosophy(run_dir / "philosophy.yaml")
    universe_name, symbols, universe_hash = _universe()
    expected = (spec.name, spec.version, spec.content_hash, spec.universe, universe_hash)
    actual = (
        manifest.philosophy_name,
        manifest.philosophy_version,
        manifest.philosophy_hash,
        universe_name,
        manifest.universe_hash,
    )
    if expected != actual:
        raise CliError("run_identity_mismatch", "manifest inputs do not match artifacts", 5)
    sessions = _scheduled_sessions(manifest.start, manifest.end, manifest.cadence)
    frames = _frames(symbols, sessions)
    benchmarks = _benchmarks([frame.execution for frame in frames], symbols)
    runner = ExperimentRunner(
        experiment=manifest,
        run_dir=run_dir,
        generate_target=_make_generator(spec),
        benchmarks=benchmarks,
        philosophy_yaml=(run_dir / "philosophy.yaml").read_text(encoding="utf-8"),
        max_turnover=spec.max_turnover,
        data_provenance=_synthetic_provenance(manifest),
    )
    return run_dir, manifest, spec, sessions, frames, runner


def _completed_dates(runner: ExperimentRunner) -> tuple[date, ...]:
    return tuple(
        date.fromisoformat(value)
        for value in sorted(runner.event_log.completed_sessions())
    )


def _replay_experiment(workspace: Path, run_id: str) -> dict[str, Any]:
    _, manifest, _, sessions, frames, runner = _load_context(workspace, run_id)
    completed = _completed_dates(runner)
    remaining = remaining_session_suffix(completed, sessions)
    final = runner.replay(frames)
    replay_events(runner.event_log.read())
    return {
        "run_id": run_id,
        "data_source": manifest.data_source,
        "processed_sessions": len(remaining),
        "skipped_sessions": len(completed),
        "total_sessions": len(sessions),
        "final_equity": str(final.total_equity),
        "message": (
            f"replayed {run_id}: {len(remaining)} processed, "
            f"{len(completed)} already complete, equity {final.total_equity}"
        ),
    }


def _paper_step(workspace: Path, run_id: str, session: date) -> dict[str, Any]:
    _, manifest, _, sessions, frames, runner = _load_context(workspace, run_id)
    completed = _completed_dates(runner)
    if session in completed:
        return {
            "run_id": run_id,
            "session": session,
            "processed": False,
            "message": f"paper step {run_id} {session}: already complete",
        }
    remaining = remaining_session_suffix(completed, sessions)
    if session not in sessions:
        raise CliError("invalid_session", f"not a scheduled session: {session}", 3)
    if not remaining or session != remaining[0]:
        expected = remaining[0] if remaining else "none"
        raise CliError("out_of_order_session", f"expected next session {expected}", 5)
    frame = next(item for item in frames if item.execution_session == session)
    final = runner.step(frame)
    replay_events(runner.event_log.read())
    return {
        "run_id": run_id,
        "session": session,
        "processed": True,
        "final_equity": str(final.total_equity),
        "data_source": manifest.data_source,
        "message": f"paper step {run_id} {session}: equity {final.total_equity}",
    }


def _evaluate_run(run_dir: Path, manifest: ExperimentManifest) -> EvaluationReport:
    equity = read_equity_csv(run_dir / "equity.csv")
    if len(equity) < 3:
        raise CliError("insufficient_history", "evaluation needs three sessions", 3)
    events = read_jsonl(run_dir / "events.jsonl")
    report = compute_evaluation(
        run_id=manifest.run_id,
        as_of=datetime.combine(equity[-1].session, time(20), tzinfo=UTC),
        equity=equity,
        fills=read_jsonl(run_dir / "fills.jsonl"),
        portfolios=read_jsonl(run_dir / "portfolio.jsonl"),
        decisions=read_jsonl(run_dir / "decisions.jsonl"),
        constraint_interventions=sum(
            event["event_type"] == "order_rejected" for event in events
        ),
    )
    write_evaluation_json(report, run_dir / "evaluation.json")
    provenance = json.loads(
        (run_dir / "data-provenance.json").read_text(encoding="utf-8")
    )
    write_report_md(
        manifest, report, run_dir / "report.md", data_provenance=provenance
    )
    return report


def _evaluate_experiment(workspace: Path, run_id: str) -> dict[str, Any]:
    run_dir, manifest, *_ = _load_context(workspace, run_id)
    report = _evaluate_run(run_dir, manifest)
    return {
        **evaluation_payload(report),
        "message": f"evaluated {run_id}: return {report.metrics.total_return:+.2%}",
    }


def _load_evaluated(workspace: Path, run_id: str):
    run_dir, manifest, *_ = _load_context(workspace, run_id)
    path = run_dir / "evaluation.json"
    if not path.is_file():
        raise CliError("evaluation_not_found", f"run is not evaluated: {run_id}", 4)
    report = EvaluationReport.model_validate_json(path.read_text(encoding="utf-8"))
    return run_dir, manifest, report


def _compare_experiments(workspace: Path, run_ids: Sequence[str]) -> dict[str, Any]:
    unique = sorted(set(run_ids))
    if len(unique) < 2:
        raise CliError("comparison_requires_two_runs", "provide at least two runs", 3)
    loaded = [_load_evaluated(workspace, run_id) for run_id in unique]
    manifests = [item[1] for item in loaded]
    identity = {
        (
            manifest.start,
            manifest.end,
            manifest.universe_hash,
            manifest.data_source,
            manifest.benchmark_source,
        )
        for manifest in manifests
    }
    date_axes = {
        tuple(point.session for point in read_equity_csv(run_dir / "equity.csv"))
        for run_dir, _, _ in loaded
    }
    if len(identity) != 1 or len(date_axes) != 1:
        raise CliError("incomparable_runs", "runs have different inputs or dates", 5)
    runs = [(manifest, report) for _, manifest, report in loaded]
    write_comparison_md(runs, workspace / "comparison.md")
    return {
        "run_ids": unique,
        "comparison": str(workspace / "comparison.md"),
        "message": f"compared {len(unique)} runs at {workspace / 'comparison.md'}",
    }


def _demo(workspace: Path, start: date, end: date) -> dict[str, Any]:
    run_ids = []
    summaries = []
    for philosophy_path in sorted(PHILOSOPHY_DIR.glob("*.yaml")):
        spec = load_philosophy(philosophy_path)
        run_id = f"exp-{spec.name}-{spec.version}"
        if not _run_dir(workspace, run_id).exists():
            _create_experiment(philosophy_path, workspace, run_id, start, end)
        replay = _replay_experiment(workspace, run_id)
        evaluation = _evaluate_experiment(workspace, run_id)
        run_ids.append(run_id)
        summaries.append(
            {
                "run_id": run_id,
                "final_equity": replay["final_equity"],
                "total_return": evaluation["metrics"]["total_return"],
            }
        )
    _compare_experiments(workspace, run_ids)
    return {
        "experiments": summaries,
        "comparison": str(workspace / "comparison.md"),
        "message": f"done: {len(run_ids)} experiments, comparison at {workspace / 'comparison.md'}",
    }


def _tagline(spec_yaml: str) -> str:
    lines = []
    for line in spec_yaml.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line.lstrip("#").strip())
    return " ".join(lines)


def _display_selected(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": row["symbol"],
        "weight": round(row["weight"], 6),
        "score": round(row["score"], 4),
        "factors": [
            {
                "name": factor["name"],
                "value": None if factor["value"] is None else round(factor["value"], 3),
                "contribution": round(factor["contribution"], 4),
            }
            for factor in row["factors"]
        ],
    }


def _display_rejected(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": row["symbol"],
        "reason": row["reason"],
        "score": None if row["score"] is None else round(row["score"], 4),
    }


def _validate_export_provenance(
    run_dir: Path,
    manifest: ExperimentManifest,
    symbols: tuple[str, ...],
) -> dict[str, Any]:
    path = run_dir / "data-provenance.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CliError(
            "run_identity_mismatch", f"invalid provenance for {run_dir.name}: {exc}", 5
        ) from exc
    if not isinstance(payload, dict):
        raise CliError(
            "run_identity_mismatch",
            f"invalid provenance for {run_dir.name}: expected an object",
            5,
        )
    equity_header = (run_dir / "equity.csv").read_text(encoding="utf-8").splitlines()[0]

    if payload.get("kind") == "synthetic":
        expected = to_jsonable(_synthetic_provenance(manifest))
        if (
            manifest.data_source != DATA_SOURCE
            or manifest.benchmark_source != BENCHMARK_SOURCE
            or equity_header != EQUITY_HEADER
            or payload != expected
        ):
            raise CliError(
                "run_identity_mismatch",
                f"synthetic provenance disagrees with {run_dir.name} artifacts",
                5,
            )
        return payload

    if payload.get("kind") != "real_market":
        raise CliError(
            "run_identity_mismatch",
            f"unsupported provenance kind for {run_dir.name}",
            5,
        )
    identity = payload.get("run_identity")
    query_payload = payload.get("query")
    if not isinstance(identity, dict) or not isinstance(query_payload, dict):
        raise CliError(
            "run_identity_mismatch",
            f"real-market provenance is incomplete for {run_dir.name}",
            5,
        )
    try:
        requested_start = date.fromisoformat(identity["requested_start"])
        requested_end = date.fromisoformat(identity["requested_end"])
        query = PriceQuery(
            tuple(query_payload["symbols"]),
            date.fromisoformat(query_payload["start"]),
            date.fromisoformat(query_payload["end"]),
            interval=query_payload["interval"],
            adjustment=query_payload["adjustment"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CliError(
            "run_identity_mismatch",
            f"invalid real-market provenance for {run_dir.name}: {exc}",
            5,
        ) from exc

    identity_hash = _identity_hash(identity)
    expected_identity = {
        "identity_version": 1,
        "actual_start": manifest.start.isoformat(),
        "actual_end": manifest.end.isoformat(),
        "transport": payload.get("transport"),
        "provider": payload.get("provider"),
        "provider_versions": payload.get("provider_versions"),
        "adjustment": payload.get("adjustment"),
        "query_hash": payload.get("query_hash"),
        "normalized_hash": payload.get("normalized_hash"),
        "philosophy_hash": manifest.philosophy_hash,
        "universe_hash": manifest.universe_hash,
        "engine_version": manifest.engine_version,
        "initial_cash": str(manifest.initial_cash),
        "slippage_bps": manifest.slippage_bps,
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "reference_method_version": REFERENCE_METHOD_VERSION,
    }
    expected_run_id = (
        f"exp-{manifest.philosophy_name}-{manifest.philosophy_version}-market-"
        f"{identity_hash[:16]}"
    )
    mismatched_identity = any(identity.get(key) != value for key, value in expected_identity.items())
    if (
        manifest.data_source != REAL_DATA_SOURCE
        or manifest.benchmark_source != REAL_BENCHMARK_SOURCE
        or equity_header != SPY_EQUITY_HEADER
        or payload.get("schema_version") != PROVENANCE_SCHEMA_VERSION
        or payload.get("validity") != "hindsight_current_universe"
        or payload.get("label") != "HINDSIGHT · ADJUSTED MARKET DATA"
        or payload.get("transport") != "openbb"
        or payload.get("provider") != "yfinance"
        or payload.get("adjustment") != "splits_and_dividends"
        or payload.get("benchmark_kind") != "no_cost_reference"
        or payload.get("execution_model_version") != EXECUTION_MODEL_VERSION
        or payload.get("reference_method_version") != REFERENCE_METHOD_VERSION
        or payload.get("run_identity_hash") != identity_hash
        or manifest.run_id != expected_run_id
        or manifest.id != expected_run_id
        or mismatched_identity
        or query.symbols != tuple(sorted((*symbols, BENCHMARK_SYMBOL)))
        or query.start != requested_start - timedelta(days=WARMUP_DAYS)
        or query.end != requested_end + timedelta(days=CALENDAR_BUFFER_DAYS)
        or query_key("openbb", "yfinance", query) != payload.get("query_hash")
    ):
        raise CliError(
            "run_identity_mismatch",
            f"real-market provenance disagrees with {run_dir.name} artifacts",
            5,
        )
    return payload


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


def _view_model(runs: list[tuple[dict[str, Any], Path]]) -> dict[str, Any]:
    universe_name, _, _ = _universe()
    _, first_dir = runs[0]
    equity_points = read_equity_csv(first_dir / "equity.csv")
    dates = [point.session.isoformat() for point in equity_points]
    proxy = [f"{point.synthetic_mega_cap_proxy_equity:.2f}" for point in equity_points]
    equal_weight = [f"{point.equal_weight_equity:.2f}" for point in equity_points]
    experiments = []

    for manifest, run_dir in runs:
        points = read_equity_csv(run_dir / "equity.csv")
        evaluation = json.loads((run_dir / "evaluation.json").read_text(encoding="utf-8"))
        rebalances = []
        decisions = read_jsonl(run_dir / "decisions.jsonl")
        if len(decisions) != len(points):
            raise ValueError("decision and equity axes have different lengths")
        provenance = _public_provenance(run_dir)
        for week, record in enumerate(decisions):
            decision_session = record["as_of"][:10]
            execution_session = dates[week]
            rebalances.append(
                {
                    "week": week,
                    "as_of": decision_session,
                    "execution_as_of": execution_session,
                    "relative_to_synthetic_mega_cap_proxy": round(
                        points[week].equity
                        / equity_points[week].synthetic_mega_cap_proxy_equity
                        - 1,
                        6,
                    ),
                    "selected": [
                        _display_selected(selection) for selection in record["selected"]
                    ],
                    "rejected": [
                        _display_rejected(rejection) for rejection in record["rejected"]
                    ],
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
                "equity": [f"{point.equity:.2f}" for point in points],
                "rebalances": rebalances,
                "evaluation": {
                    "metrics": evaluation["metrics"],
                    "fidelity": evaluation["fidelity"],
                },
                "data_provenance": provenance,
            }
        )

    sessions = [point.session for point in equity_points]
    proxy_values = [point.synthetic_mega_cap_proxy_equity for point in equity_points]
    equal_weight_values = [point.equal_weight_equity for point in equity_points]
    return {
        "dates": dates,
        "synthetic_mega_cap_proxy": proxy,
        "data_provenance": _public_provenance(first_dir),
        "equal_weight": equal_weight,
        "experiments": experiments,
        "benchmarks": {
            "synthetic_mega_cap_proxy": benchmark_metrics(
                values=proxy_values,
                sessions=sessions,
                synthetic_mega_cap_proxy_values=proxy_values,
                equal_weight_values=equal_weight_values,
            ),
            "equal_weight": benchmark_metrics(
                values=equal_weight_values,
                sessions=sessions,
                synthetic_mega_cap_proxy_values=proxy_values,
                equal_weight_values=equal_weight_values,
            ),
        },
    }


def _validated_export_runs(workspace: Path) -> list[tuple[dict[str, Any], Path]]:
    runs = []
    universe_name, symbols, universe_hash = _universe()
    for manifest_path in sorted(workspace.glob("*/manifest.json")):
        run_dir = manifest_path.parent
        missing = [name for name in EXPORT_ARTIFACTS if not (run_dir / name).is_file()]
        if missing:
            raise CliError("incomplete_run", f"{run_dir.name} missing {missing}", 4)
        manifest_model = read_manifest(manifest_path)
        if manifest_model.id != run_dir.name or manifest_model.run_id != run_dir.name:
            raise CliError("run_identity_mismatch", f"invalid run directory: {run_dir}", 3)
        spec = load_philosophy(run_dir / "philosophy.yaml")
        expected_identity = (
            spec.name,
            spec.version,
            spec.content_hash,
            spec.universe,
            universe_hash,
        )
        manifest_identity = (
            manifest_model.philosophy_name,
            manifest_model.philosophy_version,
            manifest_model.philosophy_hash,
            universe_name,
            manifest_model.universe_hash,
        )
        if expected_identity != manifest_identity:
            raise CliError(
                "run_identity_mismatch",
                f"manifest inputs do not match {run_dir.name} artifacts",
                5,
            )
        _validate_export_provenance(run_dir, manifest_model, symbols)
        points = read_equity_csv(run_dir / "equity.csv")
        if len(points) < 3:
            raise CliError("incomplete_run", f"{run_dir.name} has insufficient equity", 4)
        for name in ("decisions.jsonl", "portfolio.jsonl"):
            read_jsonl(run_dir / name)
        EvaluationReport.model_validate_json(
            (run_dir / "evaluation.json").read_text(encoding="utf-8")
        )
        runs.append((to_jsonable(manifest_model.model_dump()), run_dir))
    if not runs:
        raise CliError("no_runs", f"no evaluated runs found in {workspace}", 4)
    axes = {
        tuple(point.session for point in read_equity_csv(run_dir / "equity.csv"))
        for _, run_dir in runs
    }
    benchmark_series = {
        tuple(
            (
                point.synthetic_mega_cap_proxy_equity,
                point.equal_weight_equity,
            )
            for point in read_equity_csv(run_dir / "equity.csv")
        )
        for _, run_dir in runs
    }
    identities = {
        (
            manifest["start"],
            manifest["end"],
            manifest["universe_hash"],
            manifest["data_source"],
            manifest["benchmark_source"],
        )
        for manifest, _ in runs
    }
    if len(axes) != 1 or len(benchmark_series) != 1 or len(identities) != 1:
        raise CliError("incomparable_runs", "export runs have different inputs or dates", 5)
    return runs


def _export_workspace(workspace: Path, out: Path) -> dict[str, Any]:
    workspace_resolved = workspace.resolve()
    out_resolved = out.resolve()
    if out_resolved == workspace_resolved or out_resolved.is_relative_to(workspace_resolved):
        raise CliError("unsafe_output_path", "output cannot be inside workspace", 5)
    runs = _validated_export_runs(workspace)
    model = _view_model(runs)
    if out.exists() and not out.is_dir():
        raise CliError("unsafe_output_path", "output must be a directory", 5)
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=out.parent))
    backup = out.parent / f".{out.name}-backup"
    try:
        experiments = []
        for manifest, run_dir in runs:
            destination = temp / run_dir.name
            destination.mkdir()
            for name in EXPORT_ARTIFACTS:
                shutil.copyfile(run_dir / name, destination / name)
            experiments.append(
                {
                    "id": manifest["id"],
                    "philosophy": manifest["philosophy_name"],
                    "version": manifest["philosophy_version"],
                    "start": manifest["start"],
                    "end": manifest["end"],
                }
            )
        (temp / "index.json").write_text(
            json.dumps({"experiments": experiments}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (temp / "data.json").write_text(
            json.dumps(model, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        if backup.exists():
            shutil.rmtree(backup)
        if out.exists():
            os.replace(out, backup)
        try:
            os.replace(temp, out)
        except Exception:
            if backup.exists() and not out.exists():
                os.replace(backup, out)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    size_mb = (out / "data.json").stat().st_size / 1_000_000
    return {
        "experiments": len(runs),
        "out": str(out),
        "data_size_mb": round(size_mb, 2),
        "message": f"exported {len(runs)} experiments to {out} (data.json {size_mb:.1f} MB)",
    }


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
    *, batch: PriceBatch, identity: Mapping[str, Any]
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
    _, symbols, universe_hash = _universe()
    spec_path = PHILOSOPHY_DIR / "trend-v1.yaml"
    spec = load_philosophy(spec_path)
    _require_price_only(spec)
    query = PriceQuery(
        (*symbols, BENCHMARK_SYMBOL),
        start - timedelta(days=WARMUP_DAYS),
        end + timedelta(days=CALENDAR_BUFFER_DAYS),
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
    run_dir = workspace / run_id
    manifest_path = run_dir / "manifest.json"
    created_at = (
        read_manifest(manifest_path).created_at
        if manifest_path.is_file()
        else datetime.now(UTC)
    )
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
        created_at=created_at,
        data_source=REAL_DATA_SOURCE,
        benchmark_source=REAL_BENCHMARK_SOURCE,
        initial_cash=INITIAL_CASH,
        slippage_bps=SLIPPAGE_BPS,
    )
    provenance = _market_provenance(batch=batch, identity=identity)

    def market_history(snapshot: MarketSnapshot):
        return history_as_of(batch, symbols, snapshot.as_of)

    runner = ExperimentRunner(
        experiment=manifest,
        run_dir=run_dir,
        generate_target=_make_generator(spec, market_history),
        benchmarks=references,
        philosophy_yaml=spec_path.read_text(encoding="utf-8"),
        max_turnover=spec.max_turnover,
        data_provenance=provenance,
        reference_column="spy_equity",
    )
    runner.replay(frames)
    report = _evaluate_run(run_dir, manifest)
    write_comparison_md([(manifest, report)], workspace / "comparison.md")
    return run_dir


@philosophy_app.command("validate")
def philosophy_validate(
    path: Path,
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--format"),
) -> None:
    def action() -> dict[str, Any]:
        spec = load_philosophy(path)
        return {
            "path": str(path.resolve()),
            "name": spec.name,
            "version": spec.version,
            "universe": spec.universe,
            "content_hash": spec.content_hash,
            "message": f"OK {spec.name} {spec.version} hash={spec.content_hash}",
        }

    _execute("philosophy.validate", output_format, action)


@experiment_app.command("create")
def experiment_create(
    philosophy: Path,
    workspace: Path = typer.Option(...),
    run_id: str = typer.Option(...),
    start: datetime = typer.Option(...),
    end: datetime = typer.Option(...),
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--format"),
) -> None:
    _execute(
        "experiment.create",
        output_format,
        lambda: _create_experiment(
            philosophy, workspace, run_id, start.date(), end.date()
        ),
    )


@experiment_app.command("replay")
def experiment_replay(
    workspace: Path = typer.Option(...),
    run_id: str = typer.Option(...),
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--format"),
) -> None:
    _execute(
        "experiment.replay",
        output_format,
        lambda: _replay_experiment(workspace, run_id),
    )


@paper_app.command("step")
def paper_step(
    workspace: Path = typer.Option(...),
    run_id: str = typer.Option(...),
    session: datetime = typer.Option(...),
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--format"),
) -> None:
    _execute(
        "paper.step",
        output_format,
        lambda: _paper_step(workspace, run_id, session.date()),
    )


@experiment_app.command("evaluate")
def experiment_evaluate(
    workspace: Path = typer.Option(...),
    run_id: str = typer.Option(...),
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--format"),
) -> None:
    _execute(
        "experiment.evaluate",
        output_format,
        lambda: _evaluate_experiment(workspace, run_id),
    )


@experiment_app.command("compare")
def experiment_compare(
    workspace: Path = typer.Option(...),
    run_id: list[str] = typer.Option(...),
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--format"),
) -> None:
    _execute(
        "experiment.compare",
        output_format,
        lambda: _compare_experiments(workspace, run_id),
    )


@app.command("market-replay")
def market_replay(
    start: datetime = typer.Option(..., help="First execution date (YYYY-MM-DD)."),
    end: datetime = typer.Option(..., help="Last execution date (YYYY-MM-DD)."),
    workspace: Path = typer.Option(Path("runs/market"), help="Run output directory."),
    cache: Path = typer.Option(Path("data/cache"), help="Immutable price cache."),
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--format"),
) -> None:
    """Replay the trend philosophy on adjusted market data and fake cash."""

    def action() -> dict[str, Any]:
        run_dir = _run_market_replay(
            loader=CachedDailyPriceSource(
                OpenBBYFinancePriceSource(), PriceCache(cache)
            ),
            workspace=workspace,
            start=start.date(),
            end=end.date(),
        )
        portfolio = read_jsonl(run_dir / "portfolio.jsonl")[-1]
        return {
            "run_id": run_dir.name,
            "run_dir": str(run_dir),
            "final_equity": portfolio["total_equity"],
            "validity": "hindsight_current_universe",
            "message": (
                f"done: final equity {portfolio['total_equity']} | run {run_dir}"
            ),
        }

    _execute("market-replay", output_format, action)


@app.command()
def demo(
    workspace: Path = typer.Option(Path("runs/demo")),
    start: datetime = typer.Option(datetime(2024, 1, 5)),
    end: datetime = typer.Option(datetime(2026, 6, 26)),
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--format"),
) -> None:
    _execute(
        "demo", output_format, lambda: _demo(workspace, start.date(), end.date())
    )


@app.command()
def export(
    workspace: Path = typer.Option(...),
    out: Path = typer.Option(...),
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--format"),
) -> None:
    _execute("export", output_format, lambda: _export_workspace(workspace, out))


if __name__ == "__main__":
    app()
