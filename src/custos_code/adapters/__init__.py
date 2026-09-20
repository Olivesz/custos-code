"""Adapters turn a source (harness transcript, hook payload, vendor export) into LedgerEvents.

Contract: every adapter is a function `parse(path_or_payload) -> tuple[Session, list[LedgerEvent], str | None]`
returning the session, the chained events, and the final report text if present.
Adapters must set flags.truncated / flags.piped / flags.sidechain honestly; downstream tiers rely on them.
Golden tests live in tests/golden/<adapter>/ : real input in, expected JSONL out.

`detect` picks the adapter from the file itself so `custos-code check <path>` needs no --agent flag.
"""

from __future__ import annotations

import json
from typing import Protocol

from ..models import LedgerEvent, Session
from . import claude_code, codex, copilot, devin, machine, otel

Source = str


class Adapter(Protocol):
    def parse(self, path: str) -> tuple[Session, list[LedgerEvent], str | None]: ...


ADAPTERS: dict[Source, Adapter] = {
    "claude_code": claude_code,
    "codex": codex,
    "devin": devin,
    "machine": machine,
    "copilot": copilot,
    "otel": otel,
}

_CODEX_TYPES = frozenset(
    {
        "session_meta",
        "turn_context",
        "response_item",
        "event_msg",
        "compacted",
        "thread_rolled_back",
    }
)


def _bundle_source(obj: dict[str, object]) -> Source | None:
    """Class-R bundles and OTLP payloads are one JSON object; tell them apart by their keys."""
    if "resourceSpans" in obj or "scopeSpans" in obj:
        return "otel"
    raw = obj.get("session")
    session: dict[str, object] = raw if isinstance(raw, dict) else {}
    keys = set(obj) | {f"session.{k}" for k in session}
    # Copilot first: both bundles carry `pull_request` and a `session.session_id`, and only
    # Copilot carries a workspace or an attached tool log. Devin's tell is structured_output.
    if "pull_request" in keys and (keys & {"log", "session.workspace", "session.agent_log"}):
        return "copilot"
    if keys & {"session.structured_output", "structured_output"}:
        return "devin"
    if keys & {"session.session_id", "session_id", "pull_request", "pull_requests"}:
        return "devin"
    return None


def _line_source(rec: dict[str, object]) -> Source | None:
    if "sessionId" in rec or (rec.get("type") in ("assistant", "user") and "message" in rec):
        return "claude_code"
    if str(rec.get("type")) in _CODEX_TYPES:
        return "codex"
    if rec.get("recorder") == "custos-code-machine":
        return "machine"
    if "traceId" in rec and "spanId" in rec:
        return "otel"
    return None


def detect(path: str) -> Source:
    """Name the adapter for a file, by its shape. Raises ValueError when nothing matches."""
    with open(path, encoding="utf-8", errors="ignore") as fh:
        text = fh.read()
    if not text.strip():
        raise ValueError(f"{path} is empty")
    try:
        whole = json.loads(text)
    except json.JSONDecodeError:
        whole = None
    if isinstance(whole, dict):
        name = _bundle_source(whole)
        if name:
            return name
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            name = _line_source(rec)
            if name:
                return name
    raise ValueError(f"cannot tell which agent wrote {path}; pass --agent")


def parse(path: str, source: Source | None = None) -> tuple[Session, list[LedgerEvent], str | None]:
    """Parse a transcript with the adapter named by `source`, or the one `detect` picks."""
    name = source or detect(path)
    adapter = ADAPTERS.get(name)
    if adapter is None:
        raise ValueError(f"unknown agent {name!r}; one of {', '.join(sorted(ADAPTERS))}")
    return adapter.parse(path)
