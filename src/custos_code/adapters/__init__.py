"""Adapters turn a source (harness transcript, hook payload, vendor export) into LedgerEvents.

Contract: every adapter is a function `parse(path_or_payload) -> tuple[Session, list[LedgerEvent], str | None]`
returning the session, the chained events, and the final report text if present.
Adapters must set flags.truncated / flags.piped / flags.sidechain honestly; downstream tiers rely on them.
Golden tests live in tests/golden/<adapter>/ : real input in, expected JSONL out.

`detect` picks the adapter from the file itself so `custos-code check <path>` needs no --agent flag.

`request_and_plan` (docs/SCOPE.md §6.3, issue #58) is scope's other input besides the ledger it
already gets: what was asked, and what the agent said it would do before doing it. It lives here,
not in `scope.py`, so scope stays agent-agnostic -- it takes a request and a plan, never a
harness-specific shape.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Protocol

from ..models import EventKind, LedgerEvent, Session
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
    if rec.get("recorder") in machine.RECORDER_NAMES:
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


def _first_user_text(ledger: Sequence[LedgerEvent]) -> str:
    """The earliest non-sidechain USER event's text -- the literal ask, verbatim.

    Claude Code, Codex, and Devin (when it has a chat transcript) all write these already; nothing
    new has to be recorded (SCOPE.md §6.3's whole premise).
    """
    for event in ledger:
        if event.kind is EventKind.USER and not event.flags.sidechain and event.output:
            return event.output
    return ""


def _todo_plan(ledger: Sequence[LedgerEvent]) -> list[str]:
    """Claude Code's `TodoWrite`: the most recent call's items, oldest first.

    The latest call wins, not the first -- a plan is allowed to change, and the self-authored
    contract SCOPE.md §3 describes is whatever the agent most recently committed to, not its first
    draft.
    """
    latest: LedgerEvent | None = None
    for event in ledger:
        if event.kind is EventKind.CALL and event.tool == "TodoWrite" and not event.flags.sidechain:
            latest = event
    if latest is None:
        return []
    todos = (latest.input or {}).get("todos")
    if not isinstance(todos, list):
        return []
    out = []
    for item in todos:
        if not isinstance(item, dict):
            continue
        text = item.get("content") or item.get("activeForm") or item.get("task")
        if isinstance(text, str) and text.strip():
            out.append(text.strip())
    return out


def _stated_plan_before_first_call(ledger: Sequence[LedgerEvent]) -> list[str]:
    """Fallback plan: the last thing the agent said before it touched a tool.

    A message written before any evidence exists is a commitment it cannot later revise -- the
    same "log it has no write path to" property SCOPE.md §3 grounds the plan in. Applies to any
    adapter whose ledger has TEXT/CALL events in the shared shape (Claude Code, Codex today).
    """
    first_call_seq = next(
        (e.seq for e in ledger if e.kind is EventKind.CALL and not e.flags.sidechain), None
    )
    last_text = ""
    for event in ledger:
        if first_call_seq is not None and event.seq >= first_call_seq:
            break
        if event.kind is EventKind.TEXT and not event.flags.sidechain and event.output:
            last_text = event.output
    return [last_text] if last_text.strip() else []


def request_and_plan(
    session: Session, ledger: Sequence[LedgerEvent], report: str | None = None
) -> tuple[str, list[str]]:
    """The spec and the agent's self-authored plan, per docs/SCOPE.md §6.3.

    Deliberately reads the ledger, not the harness: Claude Code and Codex both already write a
    USER event for the request, so no per-agent branch is needed to find it. Class-R bundles
    (Copilot, Devin without a chat transcript) have no live back-and-forth to draw either from --
    there, the PR body (`report`, already the same text `custos-code check` extracts claims from)
    doubles as the spec, exactly as SCOPE.md §6.3 says, and there is no separate plan to find.

    A `TodoWrite` call is the strongest plan signal where one exists; otherwise the last thing said
    before the first tool call stands in for it (still empty for a class-R bundle, which has no
    "before the first call" boundary to speak of).
    """
    request = _first_user_text(ledger) or (report or "")
    plan = _todo_plan(ledger) or _stated_plan_before_first_call(ledger)
    return request, plan
