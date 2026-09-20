"""Codex rollout JSONL -> ledger (class F; see docs/ADAPTERS.md §3).

File: ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<session_id>.jsonl (CLI and Codex Desktop).
Observed on a real rollout on this machine:
  session_meta{id,cwd,originator,cli_version}               -> Session (key codex:<id>)
  turn_context{turn_id,cwd,sandbox_policy}                   -> turn boundary
  function_call{name=exec_command,arguments{cmd,workdir},call_id}  -> CALL tool=Bash
  exec_command_end{call_id,command[argv],cwd,exit_code,aggregated_output,duration,status,parsed_cmd}
                                                             -> RESULT with argv, exit_code, full output
  function_call_output{call_id,output "... Process exited with code N ... Original token count: T"}
                                                             -> flags.truncated only
  custom_tool_call{name=apply_patch,input "*** Begin Patch"}  -> CALL tool=Edit, paths from Add/Update/Delete File
  patch_apply_end{call_id,success,changes{path:{type,content}}} -> RESULT per path
  task_complete{turn_id,last_agent_message}                  -> TEXT (the report for the turn)
  turn_aborted / compacted / thread_rolled_back              -> integrity flags

Owner: Ananya.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
from datetime import datetime
from typing import Any

from ..ledger import MAX_OUTPUT_BYTES, chain, redact
from ..models import EventFlags, EventKind, LedgerEvent, Session
from ..parsers import is_piped

_PATH_RE = re.compile(r"(?<![\w-])((?:\.{0,2}/)?[\w.-]+(?:/[\w.-]+)+\.[A-Za-z0-9]{1,8})")
_PATCH_PATH_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$", re.MULTILINE)
_EXIT_RE = re.compile(r"Process exited with code (-?\d+)")
_TOKEN_COUNT_RE = re.compile(r"Original token count: (\d+)")

# payload.type values that carry no evidence and no report
_IGNORED = frozenset(
    {"token_count", "agent_reasoning", "agent_reasoning_delta", "agent_message_delta"}
)
# payload.type values meaning the record before this point may be summarized or discarded
_INTEGRITY = frozenset({"compacted", "thread_rolled_back"})


def _ts(rec: dict[str, Any], payload: dict[str, Any], fallback: datetime) -> datetime:
    for raw in (rec.get("timestamp"), payload.get("timestamp")):
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
    return fallback


def _abs_paths(paths: list[str], cwd: str | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        q = p if os.path.isabs(p) or not cwd else os.path.normpath(os.path.join(cwd, p))
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _command_text(value: object) -> str:
    """exec_command carries either a shell string or an argv list; the ledger stores one string."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(part) for part in value)
    return ""


def _arguments(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _obj(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _duration_ms(value: object) -> int | None:
    if isinstance(value, dict):
        secs, nanos = value.get("secs"), value.get("nanos")
        if isinstance(secs, int | float) or isinstance(nanos, int | float):
            s = float(secs) if isinstance(secs, int | float) else 0.0
            n = float(nanos) if isinstance(nanos, int | float) else 0.0
            return int(s * 1000 + n / 1_000_000)
    if isinstance(value, int | float):
        return int(float(value) * 1000)
    return None


def _patch_paths(patch: str) -> list[str]:
    return [m.group(2).strip() for m in _PATCH_PATH_RE.finditer(patch)]


class _Builder:
    """Accumulates ledger events while walking one rollout file."""

    def __init__(self) -> None:
        self.events: list[LedgerEvent] = []
        self.seq = 0
        self.session_id = ""
        self.cwd: str | None = None
        self.agent = "codex"
        self.model: str | None = None
        self.started: datetime | None = None
        self.ended: datetime = datetime.fromtimestamp(0)
        self.report: str | None = None
        self.calls: dict[str, tuple[str, dict[str, Any]]] = {}
        self.max_output_tokens: dict[str, int] = {}
        self.results: dict[str, int] = {}  # call_id -> index into self.events
        self.integrity_events = 0

    def add(self, **fields: Any) -> LedgerEvent:
        event = LedgerEvent(seq=self.seq, session_id=self.session_id, **fields)
        self.events.append(event)
        self.seq += 1
        return event

    def add_result(self, call_id: str | None, **fields: Any) -> LedgerEvent:
        event = self.add(kind=EventKind.RESULT, **fields)
        if call_id:
            self.results[call_id] = len(self.events) - 1
        return event

    def result_for(self, call_id: object) -> LedgerEvent | None:
        if isinstance(call_id, str) and call_id in self.results:
            return self.events[self.results[call_id]]
        return None

    def store_output(self, event: LedgerEvent, text: str) -> None:
        full = redact(text)
        raw = full.encode()
        if len(raw) > MAX_OUTPUT_BYTES:
            event.flags.truncated = True
        event.output = raw[:MAX_OUTPUT_BYTES].decode(errors="ignore")
        event.output_hash = hashlib.sha256(raw).hexdigest()


def _on_session_meta(b: _Builder, p: dict[str, Any], ts: datetime) -> None:
    b.session_id = str(p.get("id") or b.session_id)
    if isinstance(p.get("cwd"), str):
        b.cwd = p["cwd"]
    if isinstance(p.get("originator"), str) and p["originator"]:
        b.agent = p["originator"]
    if isinstance(p.get("model"), str):
        b.model = p["model"]


def _on_turn_context(b: _Builder, p: dict[str, Any], ts: datetime) -> None:
    cwd = p["cwd"] if isinstance(p.get("cwd"), str) else b.cwd
    b.add(
        kind=EventKind.META,
        ts=ts,
        tool=None,
        cwd=cwd,
        input=redact(
            {
                "turn_id": str(p.get("turn_id", "")),
                "sandbox_policy": json.dumps(p.get("sandbox_policy"), sort_keys=True)
                if p.get("sandbox_policy") is not None
                else "",
                "approval_policy": str(p.get("approval_policy", "")),
            }
        ),
    )


def _on_function_call(b: _Builder, p: dict[str, Any], ts: datetime) -> None:
    name = str(p.get("name", ""))
    args = _arguments(p.get("arguments"))
    call_id = str(p.get("call_id", ""))
    if name in ("exec_command", "shell", "local_shell"):
        command = _command_text(args.get("cmd") or args.get("command"))
        cwd = args["workdir"] if isinstance(args.get("workdir"), str) else b.cwd
        inp: dict[str, Any] = {"command": command}
        if isinstance(args.get("workdir"), str):
            inp["workdir"] = args["workdir"]
        tool = "Bash"
        paths = _abs_paths([m.group(1) for m in _PATH_RE.finditer(command)], cwd)
    else:
        cwd = b.cwd
        inp = args
        tool = name or "unknown"
        paths = _abs_paths(
            [v for k, v in args.items() if k in ("path", "file_path") and isinstance(v, str)], cwd
        )
    if isinstance(args.get("max_output_tokens"), int):
        b.max_output_tokens[call_id] = args["max_output_tokens"]
    b.calls[call_id] = (tool, inp)
    b.add(kind=EventKind.CALL, ts=ts, tool=tool, input=redact(inp), paths=paths, cwd=cwd)


def _on_custom_tool_call(b: _Builder, p: dict[str, Any], ts: datetime) -> None:
    name = str(p.get("name", ""))
    call_id = str(p.get("call_id", ""))
    raw = p.get("input")
    patch = raw if isinstance(raw, str) else json.dumps(raw, sort_keys=True)
    tool = "Edit" if name == "apply_patch" else (name or "unknown")
    paths = _abs_paths(_patch_paths(patch), b.cwd)
    inp: dict[str, Any] = {"patch": patch} if tool == "Edit" else {"input": patch}
    b.calls[call_id] = (tool, inp)
    b.add(kind=EventKind.CALL, ts=ts, tool=tool, input=redact(inp), paths=paths, cwd=b.cwd)


def _on_exec_command_end(b: _Builder, p: dict[str, Any], ts: datetime) -> None:
    call_id = str(p.get("call_id", ""))
    _, call_input = b.calls.get(call_id, ("Bash", {}))
    argv = _command_text(p.get("command"))
    command = argv or str(call_input.get("command", ""))
    cwd = p["cwd"] if isinstance(p.get("cwd"), str) else b.cwd
    exit_code = p.get("exit_code") if isinstance(p.get("exit_code"), int) else None
    output = _str(p.get("aggregated_output"))
    if not output:
        stdout, stderr = _str(p.get("stdout")), _str(p.get("stderr"))
        output = stdout + (("\n" + stderr) if stderr else "")
    flags = EventFlags(piped=is_piped(command) if command else False)
    event = b.add_result(
        call_id,
        ts=ts,
        tool="Bash",
        exit_code=exit_code,
        cwd=cwd,
        duration_ms=_duration_ms(p.get("duration")),
        flags=flags,
        paths=_abs_paths([m.group(1) for m in _PATH_RE.finditer(command)], cwd),
    )
    b.store_output(event, output)


def _on_patch_apply_end(b: _Builder, p: dict[str, Any], ts: datetime) -> None:
    call_id = str(p.get("call_id", ""))
    changes = _obj(p.get("changes"))
    stdout, stderr = _str(p.get("stdout")), _str(p.get("stderr"))
    success = p.get("success")
    event = b.add_result(
        call_id,
        ts=ts,
        tool="Edit",
        cwd=b.cwd,
        paths=_abs_paths([str(k) for k in changes], b.cwd),
        flags=EventFlags(error=success is False),
    )
    b.store_output(event, stdout + (("\n" + stderr) if stderr else ""))


def _on_function_call_output(b: _Builder, p: dict[str, Any], ts: datetime) -> None:
    """The model-facing view of a result: used only for truncation and a missing exit code."""
    call_id = p.get("call_id")
    raw = p.get("output")
    text = (
        raw if isinstance(raw, str) else json.dumps(raw, sort_keys=True) if raw is not None else ""
    )
    event = b.result_for(call_id)
    if event is None:
        tool = b.calls.get(str(call_id), ("Bash", {}))[0]
        event = b.add_result(str(call_id) if call_id else None, ts=ts, tool=tool, cwd=b.cwd)
        b.store_output(event, text)
    if event.exit_code is None:
        m = _EXIT_RE.search(text)
        if m:
            event.exit_code = int(m.group(1))
    m = _TOKEN_COUNT_RE.search(text)
    budget = b.max_output_tokens.get(str(call_id))
    if m and budget is not None and int(m.group(1)) > budget:
        event.flags.truncated = True


def _on_custom_tool_call_output(b: _Builder, p: dict[str, Any], ts: datetime) -> None:
    """apply_patch's secondary result: exit code and duration for an already-recorded RESULT."""
    call_id = p.get("call_id")
    payload = _arguments(p.get("output"))
    meta = _obj(payload.get("metadata"))
    event = b.result_for(call_id)
    if event is None:
        tool = b.calls.get(str(call_id), ("Edit", {}))[0]
        event = b.add_result(str(call_id) if call_id else None, ts=ts, tool=tool, cwd=b.cwd)
        b.store_output(event, _str(payload.get("output")))
    if event.exit_code is None and isinstance(meta.get("exit_code"), int):
        event.exit_code = meta["exit_code"]
    if event.duration_ms is None:
        event.duration_ms = _duration_ms(meta.get("duration_seconds"))


def _on_web_search_call(b: _Builder, p: dict[str, Any], ts: datetime) -> None:
    query = _str(p.get("query"))
    call_id = str(p.get("call_id", ""))
    b.calls[call_id] = ("WebSearch", {"query": query})
    b.add(kind=EventKind.CALL, ts=ts, tool="WebSearch", input=redact({"query": query}), cwd=b.cwd)


def _on_web_search_end(b: _Builder, p: dict[str, Any], ts: datetime) -> None:
    query = _str(p.get("query"))
    event = b.add_result(
        str(p.get("call_id")) if p.get("call_id") else None, ts=ts, tool="WebSearch", cwd=b.cwd
    )
    b.store_output(event, query)


def _on_agent_message(b: _Builder, p: dict[str, Any], ts: datetime) -> None:
    text = p.get("message")
    if isinstance(text, str) and text.strip():
        event = b.add(kind=EventKind.TEXT, ts=ts, tool=None, cwd=b.cwd)
        b.store_output(event, text)


def _on_user_message(b: _Builder, p: dict[str, Any], ts: datetime) -> None:
    text = p.get("message")
    if isinstance(text, str) and text.strip():
        event = b.add(kind=EventKind.USER, ts=ts, tool=None, cwd=b.cwd)
        b.store_output(event, text)


def _on_task_complete(b: _Builder, p: dict[str, Any], ts: datetime) -> None:
    text = p.get("last_agent_message")
    if isinstance(text, str) and text.strip():
        event = b.add(kind=EventKind.TEXT, ts=ts, tool=None, cwd=b.cwd)
        b.store_output(event, text)
        b.report = text


def _on_turn_aborted(b: _Builder, p: dict[str, Any], ts: datetime) -> None:
    """An aborted turn has no report; anything the agent said mid-turn is not one."""
    b.report = None
    b.add(
        kind=EventKind.META,
        ts=ts,
        tool=None,
        cwd=b.cwd,
        input=redact({"event": "turn_aborted", "reason": str(p.get("reason", ""))}),
    )


def _on_integrity(b: _Builder, p: dict[str, Any], ts: datetime) -> None:
    b.integrity_events += 1
    b.add(
        kind=EventKind.META,
        ts=ts,
        tool=None,
        cwd=b.cwd,
        input=redact(
            {"event": str(p.get("type", "")), "note": "history before this point may be incomplete"}
        ),
    )


_HANDLERS = {
    "session_meta": _on_session_meta,
    "turn_context": _on_turn_context,
    "function_call": _on_function_call,
    "custom_tool_call": _on_custom_tool_call,
    "function_call_output": _on_function_call_output,
    "custom_tool_call_output": _on_custom_tool_call_output,
    "exec_command_end": _on_exec_command_end,
    "patch_apply_end": _on_patch_apply_end,
    "web_search_call": _on_web_search_call,
    "web_search_end": _on_web_search_end,
    "agent_message": _on_agent_message,
    "user_message": _on_user_message,
    "task_complete": _on_task_complete,
    "turn_aborted": _on_turn_aborted,
}


def parse(path: str) -> tuple[Session, list[LedgerEvent], str | None]:
    """Parse one Codex rollout into a chained ledger.

    Returns the session, the chained events, and the report for the last completed turn
    (`task_complete.last_agent_message`), or None when the last turn was aborted or never finished.
    """
    b = _Builder()
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            payload = _obj(rec.get("payload"))
            kind = str(payload.get("type") or rec.get("type") or "")
            if kind in _IGNORED:
                continue
            ts = _ts(rec, payload, b.ended)
            b.started = b.started or ts
            b.ended = ts
            body = payload or rec
            if kind in _INTEGRITY:
                _on_integrity(b, {**body, "type": kind}, ts)
                continue
            handler = _HANDLERS.get(kind)
            if handler is not None:
                handler(b, body, ts)

    events = chain(b.events)
    session = Session(
        id=b.session_id or os.path.basename(path).removesuffix(".jsonl"),
        source="codex",
        agent=b.agent,
        model=b.model,
        started=b.started,
        ended=b.ended if b.events else None,
        cwd=b.cwd,
        n_events=len(events),
        ledger_root_hash=events[-1].hash if events else "",
        integrity_score=1.0
        if not b.integrity_events
        else max(0.0, 1.0 - 0.25 * b.integrity_events),
    )
    return session, events, b.report


def find_last_session(sessions_dir: str | None = None) -> str:
    """Most recently modified rollout under ~/.codex/sessions (for `receipts check --last`)."""
    root = sessions_dir or os.path.expanduser("~/.codex/sessions")
    files = glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True)
    if not files:
        raise FileNotFoundError(f"no Codex rollouts under {root}")
    return max(files, key=os.path.getmtime)
