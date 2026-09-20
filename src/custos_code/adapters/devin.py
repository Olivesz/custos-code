"""Devin Path C: no tool log, so the ledger is built from state (see docs/DEVIN.md, A2).

The public v1 API exposes session metadata, chat messages, `structured_output` and the PRs a
session opened -- never the shell it ran. Path C therefore reads a *bundle*: the API's
GetSessionResponse plus whatever state evidence the caller could collect. Report = PR description
+ structured_output + last Devin message. Evidence = git + CI + checkout, which is the state half
of invariant 4: an edit or commit claim can be confirmed here, while "ran the tests locally"
cannot. Path C sessions therefore carry a `no_tool_log` META row and a reduced integrity score so
Tier 1/2 answer `unrecorded` rather than inventing a witness.

Bundle (JSON, one object) -- either a raw GetSessionResponse or:
  {"session": <GetSessionResponse>,
   "git": {"root": "/abs", "branch": ..., "commits": [{"sha","subject","ts","files":[...],"stat":...}]},
   "checks": [{"name","conclusion","started_at","completed_at","url","output"}],
   "checkout": {"root": "/abs", "files": [{"path","exists","sha256","bytes"}]}}

`fetch_bundle` builds one from the v1 API (GET /v1/sessions/{id}); `nudge` is the correction loop
for a running session (POST /v1/sessions/{id}/message). Both are opt-in network calls.

Owner: Ananya.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

from ..ledger import chain, redact
from ..models import EventKind, LedgerEvent, Session
from .state import Builder, as_dict, as_list, as_text, as_ts, checkout, checks, commits, no_tool_log

API_ROOT = "https://api.devin.ai/v1"


def _messages(b: Builder, messages: list[Any], fallback: datetime) -> tuple[datetime, str | None]:
    """Chat messages are report material, not evidence: Devin's own text never witnesses an action."""
    last_devin: str | None = None
    ts = fallback
    for raw in messages:
        msg = as_dict(raw)
        ts = as_ts(msg.get("timestamp") or msg.get("created_at"), ts)
        body = as_text(msg.get("message") or msg.get("content"))
        if not body:
            continue
        kind = EventKind.USER if str(msg.get("type", "")).startswith("user") else EventKind.TEXT
        b.store_output(b.add(kind=kind, ts=ts), body)
        if kind is EventKind.TEXT:
            last_devin = body
    return ts, last_devin


def build_report(
    session: dict[str, Any], pr: dict[str, Any], last_message: str | None
) -> str | None:
    """Path C's report is the union of the three places Devin states what it did."""
    parts: list[str] = []
    body = as_text(pr.get("body") or pr.get("description"))
    if body:
        parts.append(f"## Pull request {pr.get('url') or pr.get('html_url') or ''}\n{body}".strip())
    structured = session.get("structured_output")
    if structured:
        parts.append(f"## structured_output\n{as_text(structured)}")
    if last_message:
        parts.append(f"## Final message\n{last_message}")
    return "\n\n".join(parts) if parts else None


def parse(path: str) -> tuple[Session, list[LedgerEvent], str | None]:
    with open(path, encoding="utf-8") as fh:
        bundle = json.load(fh)
    if not isinstance(bundle, dict):
        raise ValueError(f"{path}: expected a Devin bundle object")
    session = as_dict(bundle.get("session") or bundle)
    git = as_dict(bundle.get("git"))
    probes = as_dict(bundle.get("checkout"))

    session_id = str(session.get("session_id") or session.get("id") or "devin-unknown")
    started = as_ts(session.get("created_at"), datetime.fromtimestamp(0, tz=UTC))
    prs = as_list(session.get("pull_requests")) or (
        [session["pull_request"]] if session.get("pull_request") else []
    )
    pr = as_dict(prs[0]) if prs else {}
    cwd = str(git.get("root") or probes.get("root") or "") or None

    b = Builder(session_id, cwd)
    b.add(
        kind=EventKind.META,
        ts=started,
        input=redact(
            {
                "event": "session_meta",
                "path": "C",
                "status": str(session.get("status_enum", "")),
                "snapshot_id": str(session.get("snapshot_id") or ""),
                "playbook_id": str(session.get("playbook_id") or ""),
                "pull_request": str(pr.get("url") or pr.get("html_url") or ""),
            }
        ),
    )
    no_tool_log(
        b, started, "Devin public API exposes no tool calls; evidence is git + CI + checkout", "C"
    )

    ts, last_devin = _messages(b, as_list(session.get("messages")), started)
    ts = commits(b, git, ts)
    ts = checks(b, as_list(bundle.get("checks")), ts)
    checkout(b, probes, ts)

    events = chain(b.events)
    meta = Session(
        id=session_id,
        source="devin",
        agent=str(session.get("agent") or "devin"),
        model=str(session.get("model")) if session.get("model") else None,
        started=started,
        ended=ts,
        cwd=cwd,
        git_branch=str(git.get("branch") or pr.get("head") or "") or None,
        n_events=len(events),
        ledger_root_hash=events[-1].hash if events else "",
        # Path C sees state only; half the ladder is blind here and the score must say so.
        integrity_score=0.5,
    )
    return meta, events, build_report(session, pr, last_devin)


def _api(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https host
        payload = response.read().decode()
    return as_dict(json.loads(payload)) if payload.strip() else {}


def _key(token: str | None) -> str:
    key = token or os.environ.get("DEVIN_API_KEY", "")
    if not key:
        raise ValueError("no Devin API key: pass token= or set DEVIN_API_KEY")
    return key


def fetch_bundle(
    session_id: str, token: str | None = None, *, api_root: str = API_ROOT
) -> dict[str, Any]:
    """GET /v1/sessions/{id}. Nothing leaves the machine unless the caller asks for this."""
    return {"session": _api("GET", f"{api_root}/sessions/{session_id}", _key(token))}


def nudge(
    session_id: str, message: str, token: str | None = None, *, api_root: str = API_ROOT
) -> dict[str, Any]:
    """Correction loop for a running session: POST /v1/sessions/{id}/message (docs/DEVIN.md)."""
    try:
        return _api(
            "POST", f"{api_root}/sessions/{session_id}/message", _key(token), {"message": message}
        )
    except (
        urllib.error.HTTPError
    ) as exc:  # a finished session refuses messages; caller falls back to a PR comment
        raise RuntimeError(f"devin message rejected ({exc.code}): {exc.reason}") from exc
