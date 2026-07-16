"""Invariant tests for the scoring pipeline and equal-weight allocation."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from retailtrader.allocation import allocate
from retailtrader.domain import (
    EligibilityFilter,
    FundamentalObservation,
    MarketBar,
    MarketSnapshot,
    PhilosophyFactor,
    PhilosophySpec,
)
from retailtrader.scoring import generate_target

AS_OF = datetime(2026, 1, 14, 21, 0, tzinfo=UTC)
SESSION = date(2026, 1, 14)
RUN_ID = "run-test"


def bar(symbol: str, session: date = SESSION, close: float = 100.0) -> MarketBar:
    price = Decimal(str(close))
    return MarketBar(
        symbol=symbol, session=session, open=price, high=price, low=price,
        close=price, volume=1000,
    )


def fund(symbol: str, metric: str, value: float) -> FundamentalObservation:
    available = datetime(2025, 11, 15, tzinfo=UTC)
    return FundamentalObservation(
        symbol=symbol, metric=metric, value=value, period_end=date(2025, 9, 30),
        available_at=available, as_of=available,
    )


def make_spec(**overrides) -> PhilosophySpec:
    base = {
        "name": "test",
        "version": "v1",
        "universe": "test-universe",
        "cadence": "weekly",
        "filters": (),
        "factors": (
            PhilosophyFactor(name="fcf_yield", weight=0.6, direction="higher_is_better"),
            PhilosophyFactor(name="debt_to_ebitda", weight=0.4, direction="lower_is_better"),
        ),
        "min_factor_coverage": 0.75,
        "top_n": 2,
        "cash_buffer": 0.05,
        "max_position_weight": 0.5,
    }
    base.update(overrides)
    return PhilosophySpec(**base)


def fundamentals_for(values: dict[str, tuple[float, float]]) -> tuple:
    """Per symbol: (fcf_yield inputs, debt_to_ebitda inputs) via unit denominators."""
    rows = []
    for symbol, (fcf_yield, debt) in values.items():
        rows += [
            fund(symbol, "free_cash_flow", fcf_yield),
            fund(symbol, "market_cap", 1.0),
            fund(symbol, "total_debt", debt),
            fund(symbol, "ebitda", 1.0),
        ]
    return tuple(rows)


FOUR_SYMBOLS = {
    "AAA": (0.10, 1.0),
    "BBB": (0.08, 2.0),
    "CCC": (0.06, 3.0),
    "DDD": (0.04, 4.0),
}


def snapshot_for(values: dict[str, tuple[float, float]]) -> MarketSnapshot:
    return MarketSnapshot(
        as_of=AS_OF,
        bars=tuple(bar(s) for s in values),
        fundamentals=fundamentals_for(values),
    )


def test_selects_top_n_with_equal_weights_and_cash_buffer():
    portfolio, records = generate_target(make_spec(), snapshot_for(FOUR_SYMBOLS), RUN_ID)
    assert [p.symbol for p in portfolio.positions] == ["AAA", "BBB"]
    assert all(p.weight == pytest.approx(0.475) for p in portfolio.positions)
    assert portfolio.cash_weight == pytest.approx(0.05)
    total = portfolio.cash_weight + sum(p.weight for p in portfolio.positions)
    assert total == pytest.approx(1.0)


def test_scores_are_hand_calculated_percentile_blends():
    _, records = generate_target(make_spec(), snapshot_for(FOUR_SYMBOLS), RUN_ID)
    selected = {row["symbol"]: row for row in records[0]["selected"]}
    # AAA is best on both factors: mid-rank percentile (3 + 0.5)/4 = 0.875 each.
    assert selected["AAA"]["score"] == pytest.approx(0.875)
    assert selected["BBB"]["score"] == pytest.approx(0.625)
    for row in selected.values():
        contributions = sum(f["contribution"] for f in row["factors"])
        assert contributions == pytest.approx(row["score"])


def test_deterministic_regardless_of_input_order():
    snapshot = snapshot_for(FOUR_SYMBOLS)
    reversed_snapshot = MarketSnapshot(
        as_of=AS_OF,
        bars=tuple(reversed(snapshot.bars)),
        fundamentals=tuple(reversed(snapshot.fundamentals)),
    )
    portfolio_a, records_a = generate_target(make_spec(), snapshot, RUN_ID)
    portfolio_b, records_b = generate_target(make_spec(), reversed_snapshot, RUN_ID)
    assert portfolio_a == portfolio_b
    assert records_a == records_b


def test_ties_break_on_ascending_symbol():
    values = {"ZZZ": (0.10, 1.0), "MMM": (0.10, 1.0), "AAA": (0.02, 9.0)}
    spec = make_spec(top_n=1, filters=())
    portfolio, _ = generate_target(spec, snapshot_for(values), RUN_ID)
    assert [p.symbol for p in portfolio.positions] == ["MMM"]


def test_insufficient_coverage_is_rejected_but_still_scored():
    values = dict(FOUR_SYMBOLS)
    snapshot = MarketSnapshot(
        as_of=AS_OF,
        bars=tuple(bar(s) for s in [*values, "EEE"]),
        fundamentals=fundamentals_for(values)
        + (fund("EEE", "free_cash_flow", 0.20), fund("EEE", "market_cap", 1.0)),
    )
    _, records = generate_target(make_spec(), snapshot, RUN_ID)
    rejected = {row["symbol"]: row for row in records[0]["rejected"]}
    assert rejected["EEE"]["reason"] == "insufficient factor coverage"
    assert rejected["EEE"]["score"] is not None  # honest partial attribution


def test_eligibility_filter_rejects_before_selection():
    spec = make_spec(
        filters=(EligibilityFilter(metric="fcf_yield", op="gt", value=0.05),)
    )
    _, records = generate_target(spec, snapshot_for(FOUR_SYMBOLS), RUN_ID)
    rejected = {row["symbol"]: row["reason"] for row in records[0]["rejected"]}
    assert rejected["DDD"].startswith("failed filter: fcf_yield gt")
    selected = {row["symbol"] for row in records[0]["selected"]}
    assert "DDD" not in selected


def test_unavailable_filter_metric_fails_closed():
    spec = make_spec(
        factors=(
            PhilosophyFactor(name="fcf_yield", weight=1.0, direction="higher_is_better"),
        ),
        filters=(EligibilityFilter(metric="momentum_6m", op="gt", value=0.0),),
    )
    portfolio, records = generate_target(spec, snapshot_for(FOUR_SYMBOLS), RUN_ID)
    assert portfolio.positions == ()
    assert portfolio.cash_weight == pytest.approx(1.0)
    assert all(
        row["reason"].startswith("filter metric momentum_6m unavailable")
        for row in records[0]["rejected"]
    )


def test_max_position_weight_caps_and_excess_goes_to_cash():
    spec = make_spec(max_position_weight=0.15)
    portfolio, _ = generate_target(spec, snapshot_for(FOUR_SYMBOLS), RUN_ID)
    assert all(p.weight == pytest.approx(0.15) for p in portfolio.positions)
    assert portfolio.cash_weight == pytest.approx(0.70)


def test_rejections_below_cutoff_are_reported_with_scores():
    _, records = generate_target(make_spec(), snapshot_for(FOUR_SYMBOLS), RUN_ID)
    rejected = {row["symbol"]: row for row in records[0]["rejected"]}
    assert set(rejected) == {"CCC", "DDD"}
    assert all(row["reason"] == "score below cutoff" for row in rejected.values())
    assert rejected["CCC"]["score"] == pytest.approx(0.375)


def test_decision_record_matches_artifact_shape():
    _, records = generate_target(make_spec(), snapshot_for(FOUR_SYMBOLS), RUN_ID)
    assert len(records) == 1
    record = records[0]
    assert set(record) == {"as_of", "selected", "rejected"}
    assert record["as_of"] == AS_OF.isoformat()
    for row in record["selected"]:
        assert set(row) == {"symbol", "weight", "score", "factors"}
        for factor in row["factors"]:
            assert set(factor) == {"name", "value", "contribution"}
    for row in record["rejected"]:
        assert set(row) == {"symbol", "reason", "score"}


def test_price_factors_flow_through_history():
    spec = make_spec(
        factors=(
            PhilosophyFactor(name="momentum_6m", weight=1.0, direction="higher_is_better"),
        ),
        top_n=1,
        min_factor_coverage=1.0,
    )
    start = SESSION - timedelta(days=126)
    history = {
        "UPP": [
            bar("UPP", start + timedelta(days=i), 100.0 + i) for i in range(127)
        ],
        "FLT": [bar("FLT", start + timedelta(days=i), 100.0) for i in range(127)],
    }
    snapshot = MarketSnapshot(as_of=AS_OF, bars=(bar("UPP", close=226.0), bar("FLT")))
    portfolio, records = generate_target(spec, snapshot, RUN_ID, history=history)
    assert [p.symbol for p in portfolio.positions] == ["UPP"]

    # Without history a single-session snapshot cannot fabricate momentum.
    empty, empty_records = generate_target(spec, snapshot, RUN_ID)
    assert empty.positions == ()
    assert all(
        row["reason"] == "insufficient factor coverage"
        for row in empty_records[0]["rejected"]
    )


def test_allocate_empty_selection_is_all_cash():
    portfolio = allocate(make_spec(), [], RUN_ID, AS_OF)
    assert portfolio.cash_weight == 1.0
    assert portfolio.positions == ()


def test_allocate_orders_positions_by_symbol():
    portfolio = allocate(make_spec(top_n=3), ["CCC", "AAA", "BBB"], RUN_ID, AS_OF)
    assert [p.symbol for p in portfolio.positions] == ["AAA", "BBB", "CCC"]
