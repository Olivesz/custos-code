"""GitHub Copilot coding agent (class R): the agent runs on GitHub, the report is the PR body.

Copilot's session logs are linked from the commits it pushes and are downloadable as the Actions
job log for the `Copilot` workflow run; the machine-readable session export is still
NEEDS-DECISION(ananya): A3, so this adapter takes a bundle and treats the log as optional.

Bundle (JSON, one object):
  {"pull_request": {"number","title","body","html_url","head"},
   "session": {"id","agent","model","started_at","completed_at","workspace"},
   "log": [ {"type":"tool_call","name":"bash","arguments":{...},"id":...},
            {"type":"tool_result","tool_call_id":...,"exit_code":0,"output":"..."} ],
   "commits": [...], "checks": [...]}      # same shapes as the Devin bundle

`commits`/`checks` reuse the Devin Path C builders: class R evidence is class R evidence whoever
opened the PR. Without `log`, shell claims come out `unrecorded` (the record is known-incomplete),
never `unwitnessed`.

Owner: Ananya.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from ..ledger import chain, redact
from ..models import EventFlags, EventKind, LedgerEvent, Session
from ..parsers import is_piped
from .state import Builder, as_dict, as_list, as_text, as_ts, checks, commits, no_tool_log

_SHELL_TOOLS = frozenset({"bash", "shell", "run", "terminal", "execute_bash"})
_EDIT_TOOLS = frozenset(
    {"str_replace_editor", "create", "edit", "write", "apply_patch", "edit_file"}
)


def _tool_name(name: str) -> str:
    low = name.lower()
    if low in _SHELL_TOOLS:
        return "Bash"
    if low in _EDIT_TOOLS:
        return "Edit"
    if low in ("read", "view", "open"):
        return "Read"
    return name or "Tool"


def _log(b: Builder, entries: list[Any], fallback: datetime) -> datetime:
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    ts = fallback
    for raw in entries:
        entry = as_dict(raw)
        ts = as_ts(entry.get("timestamp") or entry.get("ts"), ts)
        etype = str(entry.get("type", ""))
        if etype in ("tool_call", "function_call"):
            name = _tool_name(str(entry.get("name", "")))
            args = as_dict(entry.get("arguments") or entry.get("input"))
            command = str(args.get("command") or args.get("cmd") or "")
            path = str(args.get("path") or args.get("file_path") or "")
            call_id = str(entry.get("id") or entry.get("tool_call_id") or f"call-{b.seq}")
            calls[call_id] = (name, args)
            b.add(
                kind=EventKind.CALL,
                ts=ts,
                tool=name,
                input=redact(dict(args)),
                paths=[path] if path else [],
                flags=EventFlags(piped=bool(command) and is_piped(command)),
            )
        elif etype in ("tool_result", "function_call_output"):
            call_id = str(entry.get("tool_call_id") or entry.get("id") or "")
            name, args = calls.get(call_id, ("Tool", {}))
            command = str(args.get("command") or args.get("cmd") or "")
            path = str(args.get("path") or args.get("file_path") or "")
            exit_code = entry.get("exit_code")
            event = b.add(
                kind=EventKind.RESULT,
                ts=ts,
                tool=name,
                paths=[path] if path else [],
                exit_code=int(exit_code) if isinstance(exit_code, int) else None,
                flags=EventFlags(
                    piped=bool(command) and is_piped(command),
                    error=bool(entry.get("is_error")) or bool(exit_code),
                ),
            )
            b.store_output(event, as_text(entry.get("output") or entry.get("content")))
        elif etype in ("assistant_message", "message", "text"):
            event = b.add(kind=EventKind.TEXT, ts=ts)
            b.store_output(event, as_text(entry.get("content") or entry.get("message")))
    return ts


def parse(path: str) -> tuple[Session, list[LedgerEvent], str | None]:
    with open(path, encoding="utf-8") as fh:
        bundle = json.load(fh)
    if not isinstance(bundle, dict):
        raise ValueError(f"{path}: expected a Copilot bundle object")
    session = as_dict(bundle.get("session"))
    pr = as_dict(bundle.get("pull_request"))
    git = as_dict(bundle.get("git")) or {"commits": as_list(bundle.get("commits"))}
    entries = as_list(bundle.get("log"))

    session_id = str(session.get("id") or f"copilot-pr-{pr.get('number', 'unknown')}")
    cwd = str(session.get("workspace") or git.get("root") or "") or None
    started = as_ts(
        session.get("started_at") or pr.get("created_at"), datetime.fromtimestamp(0, tz=UTC)
    )

    b = Builder(session_id, cwd)
    b.add(
        kind=EventKind.META,
        ts=started,
        input=redact(
            {
                "event": "session_meta",
                "class": "R",
                "pull_request": str(pr.get("html_url") or pr.get("url") or ""),
                "log_present": bool(entries),
            }
        ),
    )
    if not entries:
        no_tool_log(b, started, "Copilot session log not attached; evidence is git + CI only")
    ts = _log(b, entries, started)
    ts = commits(b, git, ts)
    ts = checks(b, as_list(bundle.get("checks")), ts)

    events = chain(b.events)
    body = as_text(pr.get("body"))
    report = f"## Pull request {pr.get('html_url') or ''}\n{body}".strip() if body else None
    meta = Session(
        id=session_id,
        source="copilot",
        agent=str(session.get("agent") or "copilot-coding-agent"),
        model=str(session.get("model")) if session.get("model") else None,
        started=started,
        ended=as_ts(session.get("completed_at"), ts),
        cwd=cwd,
        git_branch=str(git.get("branch") or pr.get("head") or "") or None,
        n_events=len(events),
        ledger_root_hash=events[-1].hash if events else "",
        # the bundle is assembled by the caller from the GitHub API, not signed by a harness:
        # class R cannot reach 1.0 until the log's provenance is checkable (NEEDS-DECISION: A3)
        integrity_score=0.8 if entries else 0.5,
    )
    return meta, events, report


def find_last_session(sessions_dir: str | None = None) -> str:
    root = sessions_dir or os.path.expanduser("~/.receipts/copilot")
    bundles = [os.path.join(root, f) for f in os.listdir(root)] if os.path.isdir(root) else []
    files = [p for p in bundles if p.endswith(".json") and os.path.isfile(p)]
    if not files:
        raise FileNotFoundError(f"no Copilot bundles under {root}")
    return max(files, key=os.path.getmtime)
