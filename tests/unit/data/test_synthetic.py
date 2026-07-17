"""Invariant tests for the deterministic synthetic provider."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from retailtrader import cli
from retailtrader.data import synthetic
from retailtrader.data.synthetic import (
    AVAILABILITY_LAG_DAYS,
    FUNDAMENTAL_METRICS,
    decision_snapshot_for,
    fundamentals,
    price_history,
    snapshot_for,
    trading_sessions,
)
from retailtrader.domain import (
    FundamentalObservation,
    ExperimentManifest,
    PhilosophyFactor,
    PhilosophySpec,
    TargetPortfolio,
)

AS_OF = datetime(2026, 7, 1, 20, tzinfo=UTC)


def test_series_is_deterministic():
    a = price_history("AAPL", AS_OF)
    b = price_history("AAPL", AS_OF)
    assert a == b
    assert price_history("MSFT", AS_OF) != a


def test_history_excludes_execution_bar():
    bars = price_history("AAPL", AS_OF)
    assert bars
    assert all(b.session < AS_OF.date() for b in bars)
    assert len(bars) > 253  # enough lookback for momentum_12m


def test_fundamentals_respect_availability_lag():
    obs = fundamentals("AAPL", AS_OF)
    assert obs
    for o in obs:
        assert o.available_at <= AS_OF
        assert o.available_at.date() == o.period_end + timedelta(days=AVAILABILITY_LAG_DAYS)
        assert o.availability_source == "approximated"
    assert {o.metric for o in obs} == set(FUNDAMENTAL_METRICS)


def test_snapshot_builds_for_session():
    session = trading_sessions(date(2026, 6, 1), date(2026, 6, 30))[0]
    snap = snapshot_for(("AAPL", "MSFT", "NVDA"), session)
    assert {b.symbol for b in snap.bars} == {"AAPL", "MSFT", "NVDA"}
    assert snap.fundamentals
    assert snap.as_of.date() == session


def test_decision_snapshot_cannot_see_execution_bar_or_late_fundamental(monkeypatch):
    execution_session = date(2026, 6, 15)
    late = FundamentalObservation(
        symbol="LOOK",
        metric="revenue",
        value=999.0,
        period_end=date(2026, 3, 31),
        available_at=datetime.combine(execution_session, time(15), tzinfo=UTC),
        as_of=datetime.combine(execution_session, time(15), tzinfo=UTC),
        availability_source="approximated",
    )
    monkeypatch.setattr(synthetic, "_fundamental_series", lambda symbol: (late,))

    decision = decision_snapshot_for(("LOOK",), execution_session)
    execution = snapshot_for(("LOOK",), execution_session)

    assert decision.as_of.date() < execution_session
    assert all(bar.session < execution_session for bar in decision.bars)
    assert late not in decision.fundamentals
    assert any(obs.available_at == late.available_at for obs in execution.fundamentals)


def test_demo_target_generator_scores_the_prior_decision_snapshot(monkeypatch):
    execution_session = date(2026, 6, 15)
    decision_input = decision_snapshot_for(("AAPL",), execution_session)
    captured = {}

    def capture_target(spec, snapshot, run_id, *, history):
        captured["snapshot"] = snapshot
        captured["history"] = history
        return (
            TargetPortfolio(
                run_id=run_id,
                as_of=snapshot.as_of,
                cash_weight=1.0,
                positions=(),
            ),
            [],
        )

    monkeypatch.setattr(cli, "generate_target", capture_target)
    spec = PhilosophySpec(
        name="point-in-time",
        version="v1",
        universe="synthetic",
        cadence="weekly",
        factors=(
            PhilosophyFactor(
                name="momentum_6m", weight=1.0, direction="higher_is_better"
            ),
        ),
        min_factor_coverage=1.0,
        top_n=1,
        cash_buffer=0.0,
        max_position_weight=1.0,
    )

    manifest = ExperimentManifest(
        id="point-in-time",
        run_id="point-in-time",
        philosophy_name=spec.name,
        philosophy_version=spec.version,
        philosophy_hash="hash",
        universe_hash="universe",
        cadence="weekly",
        start=date(2026, 6, 1),
        end=date(2026, 6, 30),
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        initial_cash=Decimal("100000.00"),
        slippage_bps=5,
    )
    cli._make_generator(spec)(manifest, decision_input)

    decision = captured["snapshot"]
    assert decision == decision_input
    assert decision.as_of.date() < execution_session
    assert all(bar.session < execution_session for bar in decision.bars)
    assert all(
        bar.session <= decision.as_of.date()
        for history in captured["history"].values()
        for bar in history
    )
