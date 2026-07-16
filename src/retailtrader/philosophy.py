"""Strict philosophy YAML loading.

Loads a philosophy specification through ``yaml.safe_load`` into the frozen
:class:`~retailtrader.domain.PhilosophySpec` contract. Unknown keys, unknown
factor or filter metrics, unsupported operators, and duplicate factors are
all rejected. No expression evaluation, no code import from YAML.

The returned spec carries a deterministic ``content_hash``: the SHA-256 of
its canonical JSON representation (sorted keys, compact separators), so the
same philosophy content always hashes identically regardless of YAML
formatting or key order.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from retailtrader.domain import PhilosophySpec
from retailtrader.factors import CATALOG


class PhilosophyError(ValueError):
    """Raised when a philosophy YAML fails validation."""


def compute_content_hash(spec: PhilosophySpec) -> str:
    """SHA-256 of the spec's canonical JSON, excluding the hash field itself."""
    payload = spec.model_dump(mode="json", exclude={"content_hash"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_philosophy(raw: object, *, source: str = "<memory>") -> PhilosophySpec:
    """Validate an already-loaded YAML document into a hashed PhilosophySpec."""
    if not isinstance(raw, dict):
        raise PhilosophyError(f"{source}: philosophy YAML must be a mapping")
    if "content_hash" in raw:
        raise PhilosophyError(f"{source}: content_hash is computed, not declared")
    try:
        spec = PhilosophySpec.model_validate(raw)
    except ValidationError as exc:
        raise PhilosophyError(f"{source}: {exc}") from exc

    unknown_factors = sorted(f.name for f in spec.factors if f.name not in CATALOG)
    if unknown_factors:
        raise PhilosophyError(f"{source}: unknown factors: {', '.join(unknown_factors)}")
    unknown_metrics = sorted(
        {f.metric for f in spec.filters if f.metric not in CATALOG}
    )
    if unknown_metrics:
        raise PhilosophyError(
            f"{source}: unknown filter metrics: {', '.join(unknown_metrics)}"
        )

    return spec.model_copy(update={"content_hash": compute_content_hash(spec)})


def load_philosophy(path: str | Path) -> PhilosophySpec:
    """Load, strictly validate, and hash a philosophy YAML file."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return parse_philosophy(raw, source=str(path))
