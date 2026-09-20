"""Shared state-evidence builders for the class-R adapters (Devin cloud, Copilot, any PR bot).

Class R has no tool log by default: the agent ran elsewhere and left a PR behind. What it does
leave is state -- commits, CI conclusions, and the files in a checkout -- which is the state half
of the two-evidence rule, so edit/create/commit claims are settleable here and shell claims are
not. Devin and Copilot differ only in how the report and metadata are fetched, so the evidence
side lives here once.

Owner: Ananya.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

from ..ledger import MAX_OUTPUT_BYTES, redact
from ..models import EventFlags, EventKind, LedgerEvent

FAILED_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
)


def as_ts(value: object, fallback: datetime) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return fallback
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    return fallback


def as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def as_list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def abs_path(path: str, root: str | None) -> str:
    return path if os.path.isabs(path) or not root else os.path.normpath(os.path.join(root, path))


class Builder:
    """Accumulates ledger events for one session, numbering and hashing output as it goes."""

    def __init__(self, session_id: str, cwd: str | None) -> None:
        self.events: list[LedgerEvent] = []
        self.seq = 0
        self.session_id = session_id
        self.cwd = cwd

    def add(self, **fields: Any) -> LedgerEvent:
        event = LedgerEvent(seq=self.seq, session_id=self.session_id, cwd=self.cwd, **fields)
        self.events.append(event)
        self.seq += 1
        return event

    def store_output(self, event: LedgerEvent, text: str) -> None:
        blob = str(redact(text))
        raw = blob.encode()
        event.output_hash = hashlib.sha256(raw).hexdigest()
        event.output = raw[:MAX_OUTPUT_BYTES].decode(errors="ignore")
        event.flags.truncated = event.flags.truncated or len(raw) > MAX_OUTPUT_BYTES


def commits(b: Builder, git: dict[str, Any], fallback: datetime) -> datetime:
    """Commits satisfy invariant 5 for edit/create claims: the transcript alone never could."""
    root = str(git.get("root") or b.cwd or "") or None
    ts = fallback
    for raw in as_list(git.get("commits")):
        commit = as_dict(raw)
        ts = as_ts(commit.get("ts") or commit.get("date"), ts)
        files = [abs_path(str(f), root) for f in as_list(commit.get("files"))]
        event = b.add(
            kind=EventKind.RESULT,
            ts=ts,
            tool="Git",
            exit_code=0,
            paths=files,
            input=redact(
                {"sha": str(commit.get("sha", "")), "subject": str(commit.get("subject", ""))}
            ),
        )
        b.store_output(
            event, as_text(commit.get("stat") or commit.get("body") or commit.get("subject"))
        )
    return ts


def checks(b: Builder, runs: list[Any], fallback: datetime) -> datetime:
    """CI runs are the outcome evidence class R has; a failed conclusion is positive evidence."""
    ts = fallback
    for raw in runs:
        check = as_dict(raw)
        ts = as_ts(check.get("completed_at") or check.get("started_at"), ts)
        conclusion = str(check.get("conclusion") or check.get("status") or "").lower()
        failed = conclusion in FAILED_CONCLUSIONS
        started = as_ts(check.get("started_at"), ts)
        duration = int((ts - started).total_seconds() * 1000) if ts > started else None
        meta = redact({"name": str(check.get("name", "")), "url": str(check.get("url", ""))})
        b.add(kind=EventKind.CALL, ts=started, tool="CI", input=meta)
        event = b.add(
            kind=EventKind.RESULT,
            ts=ts,
            tool="CI",
            duration_ms=duration,
            input=meta,
            exit_code=0 if conclusion == "success" else 1 if failed else None,
            flags=EventFlags(error=failed),
        )
        b.store_output(event, as_text(check.get("output") or check.get("logs") or conclusion))
    return ts


def checkout(b: Builder, probes: dict[str, Any], fallback: datetime) -> None:
    """Filesystem probes taken after the session: `exists: false` is positive evidence of absence."""
    root = str(probes.get("root") or b.cwd or "") or None
    for raw in as_list(probes.get("files")):
        probe = as_dict(raw)
        path = abs_path(str(probe.get("path", "")), root)
        if not path:
            continue
        exists = bool(probe.get("exists", True))
        event = b.add(
            kind=EventKind.RESULT,
            ts=as_ts(probe.get("ts"), fallback),
            tool="Read",
            paths=[path],
            exit_code=0 if exists else 1,
            flags=EventFlags(error=not exists),
            input={"path": path, "probe": "stat"},
        )
        b.store_output(
            event,
            json.dumps(
                {"exists": exists, "sha256": probe.get("sha256"), "bytes": probe.get("bytes")},
                sort_keys=True,
            ),
        )


def no_tool_log(b: Builder, ts: datetime, note: str, evidence_class: str = "R") -> LedgerEvent:
    """Record the instrumentation gap explicitly, so Tier 1/2 answer `unrecorded`, not `unwitnessed`."""
    return b.add(
        kind=EventKind.META,
        ts=ts,
        input={"event": "no_tool_log", "class": evidence_class, "note": note},
    )
