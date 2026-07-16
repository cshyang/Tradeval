"""Invariant tests for strict philosophy YAML loading."""

import re
from pathlib import Path

import pytest
import yaml

from retailtrader.domain import PhilosophySpec
from retailtrader.philosophy import PhilosophyError, load_philosophy, parse_philosophy

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = [
    REPO_ROOT / "philosophies" / "quality-value-v1.yaml",
    REPO_ROOT / "philosophies" / "garp-v1.yaml",
    REPO_ROOT / "philosophies" / "trend-v1.yaml",
]


def base_spec() -> dict:
    return {
        "name": "test-philosophy",
        "version": "v1",
        "universe": "us-large-cap-30",
        "cadence": "weekly",
        "filters": [{"metric": "debt_to_ebitda", "op": "lte", "value": 5.0}],
        "factors": [
            {"name": "roic", "weight": 0.6, "direction": "higher_is_better"},
            {"name": "fcf_yield", "weight": 0.4, "direction": "higher_is_better"},
        ],
        "min_factor_coverage": 0.75,
        "top_n": 8,
        "cash_buffer": 0.05,
        "max_position_weight": 0.15,
    }


def load_from(tmp_path: Path, doc: dict, name: str = "spec.yaml") -> PhilosophySpec:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(doc))
    return load_philosophy(path)


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.stem)
def test_templates_load_with_stable_hashes(path: Path):
    first = load_philosophy(path)
    second = load_philosophy(path)
    assert isinstance(first, PhilosophySpec)
    assert first.content_hash == second.content_hash
    assert re.fullmatch(r"[0-9a-f]{64}", first.content_hash)


def test_template_hashes_are_distinct():
    hashes = {load_philosophy(p).content_hash for p in TEMPLATES}
    assert len(hashes) == len(TEMPLATES)


def test_hash_is_independent_of_yaml_formatting(tmp_path: Path):
    doc = base_spec()
    reordered = dict(reversed(list(doc.items())))
    assert (
        load_from(tmp_path, doc, "a.yaml").content_hash
        == load_from(tmp_path, reordered, "b.yaml").content_hash
    )


def test_hash_changes_when_content_changes(tmp_path: Path):
    changed = base_spec()
    changed["top_n"] = 5
    assert (
        load_from(tmp_path, base_spec(), "a.yaml").content_hash
        != load_from(tmp_path, changed, "b.yaml").content_hash
    )


def test_rejects_unknown_top_level_key(tmp_path: Path):
    doc = base_spec()
    doc["custom_python"] = "import os"
    with pytest.raises(PhilosophyError):
        load_from(tmp_path, doc)


def test_rejects_unknown_factor(tmp_path: Path):
    doc = base_spec()
    doc["factors"][0]["name"] = "sentiment_score"
    with pytest.raises(PhilosophyError, match="unknown factors"):
        load_from(tmp_path, doc)


def test_rejects_arbitrary_expression_as_metric(tmp_path: Path):
    doc = base_spec()
    doc["filters"] = [{"metric": "close / open - 1", "op": "gt", "value": 0.0}]
    with pytest.raises(PhilosophyError, match="unknown filter metrics"):
        load_from(tmp_path, doc)


def test_rejects_unsupported_operator(tmp_path: Path):
    doc = base_spec()
    doc["filters"][0]["op"] = "matches_regex"
    with pytest.raises(PhilosophyError):
        load_from(tmp_path, doc)


def test_rejects_negative_factor_weight(tmp_path: Path):
    doc = base_spec()
    doc["factors"][0]["weight"] = -0.5
    with pytest.raises(PhilosophyError):
        load_from(tmp_path, doc)


def test_rejects_duplicate_factors(tmp_path: Path):
    doc = base_spec()
    doc["factors"][1]["name"] = "roic"
    with pytest.raises(PhilosophyError, match="duplicate factor"):
        load_from(tmp_path, doc)


@pytest.mark.parametrize(
    "missing", ["top_n", "cash_buffer", "max_position_weight", "min_factor_coverage"]
)
def test_rejects_missing_portfolio_controls(tmp_path: Path, missing: str):
    doc = base_spec()
    del doc[missing]
    with pytest.raises(PhilosophyError):
        load_from(tmp_path, doc)


def test_rejects_declared_content_hash(tmp_path: Path):
    doc = base_spec()
    doc["content_hash"] = "deadbeef"
    with pytest.raises(PhilosophyError, match="computed, not declared"):
        load_from(tmp_path, doc)


def test_rejects_non_mapping_document(tmp_path: Path):
    path = tmp_path / "list.yaml"
    path.write_text("- just\n- a\n- list\n")
    with pytest.raises(PhilosophyError, match="must be a mapping"):
        load_philosophy(path)


def test_between_requires_pair_and_scalar_ops_reject_pairs():
    doc = base_spec()
    doc["filters"] = [{"metric": "roic", "op": "between", "value": [0.1, 0.3]}]
    spec = parse_philosophy(doc)
    assert spec.filters[0].value == (0.1, 0.3)

    doc["filters"] = [{"metric": "roic", "op": "gt", "value": [0.1, 0.3]}]
    with pytest.raises(PhilosophyError):
        parse_philosophy(doc)

    doc["filters"] = [{"metric": "roic", "op": "between", "value": 0.1}]
    with pytest.raises(PhilosophyError):
        parse_philosophy(doc)
