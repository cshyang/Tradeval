"""Immutable cache for raw and normalized SEC company facts."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from retailtrader.data.protocol import canonical_json
from retailtrader.data.sec import (
    COMPANYFACTS_ENDPOINT_VERSION,
    SecCompanyFacts,
    SecDataError,
    normalize_cik,
)

FailureHook = Callable[[str], None]


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _entry_key(cik: str) -> str:
    identity = canonical_json(
        {"cik": cik, "endpoint_version": COMPANYFACTS_ENDPOINT_VERSION}
    )
    return hashlib.sha256(identity.encode()).hexdigest()


class FundamentalCache:
    """Store one complete, immutable SEC response for each CIK and endpoint version."""

    def __init__(self, root: Path, failure_hook: FailureHook | None = None) -> None:
        self.root = Path(root)
        self.failure_hook = failure_hook

    def _fail(self, point: str) -> None:
        if self.failure_hook is not None:
            self.failure_hook(point)

    def entry_path(self, cik: str | int) -> Path:
        normalized_cik = normalize_cik(cik)
        return self.root / "sec" / "companyfacts" / _entry_key(normalized_cik)

    @contextmanager
    def _locked(self, parent: Path) -> Iterator[None]:
        parent.mkdir(parents=True, exist_ok=True)
        lock_path = parent / ".cache.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def load(self, cik: str | int) -> SecCompanyFacts | None:
        normalized_cik = normalize_cik(cik)
        entry = self.entry_path(normalized_cik)
        with self._locked(entry.parent):
            if not entry.exists():
                return None
            _fsync_directory(entry.parent)
            return self._load_entry(entry, normalized_cik)

    def _load_entry(self, entry: Path, cik: str) -> SecCompanyFacts:
        try:
            raw_path = entry / "companyfacts.json"
            metadata_path = entry / "metadata.json"
            if not entry.is_dir() or not raw_path.is_file() or not metadata_path.is_file():
                raise SecDataError(f"incomplete fundamental cache entry: {entry}")
            raw_json = raw_path.read_text(encoding="utf-8").rstrip("\n")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            required = {
                "cache_schema_version",
                "endpoint_version",
                "cik",
                "entity_name",
                "retrieved_at",
                "raw_hash",
                "normalized_hash",
                "observation_count",
            }
            if not isinstance(metadata, dict) or set(metadata) != required:
                raise SecDataError("fundamental cache metadata fields do not match schema")
            if (
                metadata["cache_schema_version"] != 1
                or metadata["endpoint_version"] != COMPANYFACTS_ENDPOINT_VERSION
                or metadata["cik"] != cik
            ):
                raise SecDataError("fundamental cache identity mismatch")
            raw_hash = hashlib.sha256(raw_json.encode()).hexdigest()
            if raw_hash != metadata["raw_hash"]:
                raise SecDataError("fundamental cache raw hash mismatch")
            payload = json.loads(raw_json)
            document = SecCompanyFacts.create(
                cik=cik,
                payload=payload,
                retrieved_at=datetime.fromisoformat(metadata["retrieved_at"]),
            )
            if (
                document.raw_hash != metadata["raw_hash"]
                or document.normalized_hash != metadata["normalized_hash"]
                or document.entity_name != metadata["entity_name"]
                or len(document.observations) != metadata["observation_count"]
            ):
                raise SecDataError("fundamental cache normalized content mismatch")
            return document
        except SecDataError:
            raise
        except Exception as exc:
            raise SecDataError(f"invalid fundamental cache entry {entry}: {exc}") from exc

    def store(self, document: SecCompanyFacts) -> None:
        if not isinstance(document, SecCompanyFacts):
            raise TypeError("document must be SecCompanyFacts")
        target = self.entry_path(document.cik)
        parent = target.parent
        with self._locked(parent):
            if target.exists():
                if self._load_entry(target, document.cik) != document:
                    raise SecDataError(
                        "conflicting immutable fundamental cache entry for CIK"
                    )
                _fsync_directory(parent)
                return

            temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=parent))
            published = False
            try:
                raw_path = temporary / "companyfacts.json"
                raw_path.write_text(document.raw_json + "\n", encoding="utf-8")
                _fsync_file(raw_path)
                metadata = {
                    "cache_schema_version": 1,
                    "endpoint_version": COMPANYFACTS_ENDPOINT_VERSION,
                    "cik": document.cik,
                    "entity_name": document.entity_name,
                    "retrieved_at": document.retrieved_at.isoformat(),
                    "raw_hash": document.raw_hash,
                    "normalized_hash": document.normalized_hash,
                    "observation_count": len(document.observations),
                }
                metadata_path = temporary / "metadata.json"
                metadata_path.write_text(canonical_json(metadata) + "\n", encoding="utf-8")
                _fsync_file(metadata_path)
                _fsync_directory(temporary)
                self._fail("before_publish")
                try:
                    os.rename(temporary, target)
                    published = True
                except OSError:
                    if not target.exists():
                        raise
                    if self._load_entry(target, document.cik) != document:
                        raise SecDataError(
                            "conflicting immutable fundamental cache entry for CIK"
                        ) from None
                self._fail("after_publish")
                _fsync_directory(parent)
            finally:
                if not published and temporary.exists():
                    shutil.rmtree(temporary, ignore_errors=True)
