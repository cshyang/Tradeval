"""Crash-safe, immutable journals for completed simulation transitions."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

FailureHook = Callable[[str], None]


class TransitionIntegrityError(RuntimeError):
    """A session journal already exists with different content."""


def _canonical_bytes(transition: Mapping[str, Any]) -> bytes:
    return (json.dumps(transition, indent=2, sort_keys=True) + "\n").encode()


def _session_key(session: date | str) -> str:
    value = session.isoformat() if isinstance(session, date) else session
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid execution session: {value}") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"execution session must be an ISO date: {value}")
    return value


class TransitionStore:
    """Own one atomically committed source-of-truth journal per session."""

    def __init__(self, run_dir: Path, failure_hook: FailureHook | None = None) -> None:
        self.directory = run_dir / "transitions"
        self.failure_hook = failure_hook

    def _fail(self, point: str) -> None:
        if self.failure_hook is not None:
            self.failure_hook(point)

    def path(self, session: date | str) -> Path:
        return self.directory / f"{_session_key(session)}.json"

    def _fsync_directory(self) -> None:
        self._fail("before_parent_fsync")
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fail("after_parent_fsync")

    def commit(self, session: date | str, transition: Mapping[str, Any]) -> None:
        """Durably publish a journal, accepting only exact idempotent retries."""
        session_key = _session_key(session)
        if transition.get("session") != session_key:
            raise ValueError("transition session does not match journal session")
        content = _canonical_bytes(transition)
        target = self.path(session_key)
        self.directory.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() == content:
                self._fsync_directory()
                return
            raise TransitionIntegrityError(
                f"conflicting transition journal for session {session_key}"
            )

        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.directory, prefix=f".{session_key}.", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())

            self._fail("before_journal_replace")
            try:
                # Both paths are in the journal directory, so creating the hard
                # link atomically publishes the complete, fsynced file without
                # ever overwriting another writer's journal.
                os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() == content:
                    temporary.unlink()
                    temporary = None
                    self._fsync_directory()
                    return
                raise TransitionIntegrityError(
                    f"conflicting transition journal for session {session_key}"
                ) from None
            temporary.unlink()
            temporary = None
            self._fail("after_journal_replace")
            self._fsync_directory()
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def read_all(self) -> list[dict[str, Any]]:
        """Read committed journals in execution-session order."""
        if not self.directory.exists():
            return []
        journals = []
        for path in sorted(self.directory.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("session") != path.stem:
                raise TransitionIntegrityError(
                    f"journal session does not match filename: {path.name}"
                )
            journals.append(payload)
        return journals

    def completed_sessions(self) -> set[str]:
        return {transition["session"] for transition in self.read_all()}
