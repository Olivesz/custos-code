"""Claude Code session JSONL -> ledger (class F, post-hoc). See docs/ADAPTERS.md §2.

Observed on the transcripts on this machine (299 sessions surveyed):
- records of interest have `type` in {assistant, user} and `message.content` as a list of blocks;
  every such record carries `timestamp`, `sessionId`, `cwd`, `gitBranch`, `uuid`, `parentUuid`,
  `version`, and `isSidechain`.
- assistant blocks: `text`, `thinking`, `tool_use{id, name, input}`.
- user blocks: `tool_result{tool_use_id, content, is_error}`; the record also carries `toolUseResult`:
    Bash  -> {stdout, stderr, interrupted, isImage, noOutputExpected[, gitOperation]}   (no exit code)
    Edit  -> {filePath, oldString, newString, replaceAll, originalFile, structuredPatch}
    Write -> {filePath, content, originalFile, structuredPatch, type, userModified}
    Read  -> {file{filePath, ...}, type}
- the report for a turn is the last assistant `text` block before the next human user message;
  the session report is the last one in the file.

Owner: Oliver.
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

_PIPE_RE = re.compile(r"\|\s*(head|tail|grep|wc|less|more|cut|awk|sed)\b|2>\s*/dev/null|>\s*/dev/null|--silent\b|--quiet\b")
_PATH_RE = re.compile(r"(?<![\w-])((?:\.{0,2}/)?[\w.-]+(?:/[\w.-]+)+\.[A-Za-z0-9]{1,8})")


def _ts(rec: dict[str, Any]) -> datetime:
    raw = rec.get("timestamp")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.fromtimestamp(0)


def _paths_from_input(tool: str, inp: dict[str, Any], cwd: str | None) -> list[str]:
    out: list[str] = []
    for key in ("file_path", "path", "notebook_path"):
        v = inp.get(key)
        if isinstance(v, str) and v:
            out.append(v)
    if tool == "Bash":
        cmd = inp.get("command")
        if isinstance(cmd, str):
            out.extend(m.group(1) for m in _PATH_RE.finditer(cmd))
    seen: set[str] = set()
    res: list[str] = []
    for p in out:
        q = p if os.path.isabs(p) or not cwd else os.path.normpath(os.path.join(cwd, p))
        if q not in seen:
            seen.add(q)
            res.append(q)
    return res


def _result_text(block: dict[str, Any], tur: Any) -> str:
    if isinstance(tur, dict):
        so, se = tur.get("stdout"), tur.get("stderr")
        if isinstance(so, str) or isinstance(se, str):
            return (so or "") + (("\n" + se) if se else "")
        if isinstance(tur.get("content"), str):
            return str(tur["content"])
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
    return ""


def _flags_from_result(tool: str | None, inp: dict[str, Any] | None, block: dict[str, Any], tur: Any, sidechain: bool) -> EventFlags:
    f = EventFlags(sidechain=sidechain)
    if bool(block.get("is_error")):
        f.error = True
    if isinstance(tur, dict) and bool(tur.get("interrupted")):
        f.interrupted = True
    if tool == "Bash" and inp and isinstance(inp.get("command"), str) and _PIPE_RE.search(inp["command"]):
        f.piped = True
    return f


def parse(path: str) -> tuple[Session, list[LedgerEvent], str | None]:
    """Parse one Claude Code transcript into a chained ledger.

    Returns the session, all events (main chain and sidechains, flagged), and the session's final
    report text (last assistant text block on the main chain), or None if there is none.
    """
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    events: list[LedgerEvent] = []
    session_id = ""
    cwd: str | None = None
    branch: str | None = None
    started: datetime | None = None
    ended: datetime | None = None
    report: str | None = None
    seq = 0

    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") not in ("assistant", "user"):
                continue
            msg = rec.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            session_id = session_id or str(rec.get("sessionId", ""))
            cwd = cwd or rec.get("cwd")
            branch = branch or rec.get("gitBranch")
            ts = _ts(rec)
            started = started or ts
            ended = ts
            sidechain = bool(rec.get("isSidechain"))
            rec_cwd = rec.get("cwd") if isinstance(rec.get("cwd"), str) else cwd

            for block in content:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if rec["type"] == "assistant" and kind == "tool_use":
                    tool = str(block.get("name", ""))
                    raw_inp = block.get("input")
                    inp: dict[str, Any] = dict(raw_inp) if isinstance(raw_inp, dict) else {}
                    calls[str(block.get("id"))] = (tool, inp)
                    events.append(LedgerEvent(
                        seq=seq, ts=ts, session_id=session_id, kind=EventKind.CALL, tool=tool,
                        input=redact(inp), paths=_paths_from_input(tool, inp, rec_cwd), cwd=rec_cwd,
                        flags=EventFlags(sidechain=sidechain),
                    ))
                    seq += 1
                elif rec["type"] == "user" and kind == "tool_result":
                    call = calls.get(str(block.get("tool_use_id")))
                    rtool: str | None = call[0] if call else None
                    rinp: dict[str, Any] = call[1] if call else {}
                    tur = rec.get("toolUseResult")
                    full = _result_text(block, tur)
                    full = redact(full)
                    flags = _flags_from_result(rtool, rinp, block, tur, sidechain)
                    paths: list[str] = []
                    if isinstance(tur, dict):
                        fp: object = tur.get("filePath")
                        if fp is None and isinstance(tur.get("file"), dict):
                            fp = tur["file"].get("filePath")
                        if isinstance(fp, str):
                            paths.append(fp)
                    if len(full.encode()) > MAX_OUTPUT_BYTES:
                        flags.truncated = True
                    events.append(LedgerEvent(
                        seq=seq, ts=ts, session_id=session_id, kind=EventKind.RESULT, tool=rtool,
                        output=full.encode()[:MAX_OUTPUT_BYTES].decode(errors="ignore"),
                        output_hash=hashlib.sha256(full.encode()).hexdigest(),
                        exit_code=None, paths=paths, cwd=rec_cwd, flags=flags,
                    ))
                    seq += 1
                elif rec["type"] == "assistant" and kind == "text":
                    text = str(block.get("text", ""))
                    if not text.strip():
                        continue
                    events.append(LedgerEvent(
                        seq=seq, ts=ts, session_id=session_id, kind=EventKind.TEXT, tool=None,
                        output=redact(text), cwd=rec_cwd, flags=EventFlags(sidechain=sidechain),
                    ))
                    seq += 1
                    if not sidechain:
                        report = text
                elif rec["type"] == "user" and kind == "text":
                    events.append(LedgerEvent(
                        seq=seq, ts=ts, session_id=session_id, kind=EventKind.USER, tool=None,
                        output=redact(str(block.get("text", "")))[:MAX_OUTPUT_BYTES], cwd=rec_cwd,
                        flags=EventFlags(sidechain=sidechain),
                    ))
                    seq += 1

    events = chain(events)
    session = Session(
        id=session_id or os.path.basename(path).removesuffix(".jsonl"), source="claude_code",
        agent="claude-code", started=started, ended=ended, cwd=cwd, git_branch=branch,
        n_events=len(events), ledger_root_hash=events[-1].hash if events else "",
    )
    return session, events, report


def find_last_session(projects_dir: str | None = None) -> str:
    """Most recently modified transcript under ~/.claude/projects (for `receipts check --last`)."""
    root = projects_dir or os.path.expanduser("~/.claude/projects")
    files = glob.glob(os.path.join(root, "*", "*.jsonl"))
    if not files:
        raise FileNotFoundError(f"no Claude Code transcripts under {root}")
    return max(files, key=os.path.getmtime)


def find_sessions(projects_dir: str | None = None, limit: int | None = None) -> list[str]:
    """Local transcripts, newest first. Powers `receipts scan`.

    The point of scanning real history rather than a fixture: a staged trap is only caught when the
    agent takes the bait, and a careful agent simply does not. Real sessions contain the failures
    that actually happen -- a sample reported as a total, a remembered test count, a "verified
    working" that skipped the one command that failed.
    """
    root = projects_dir or os.path.expanduser("~/.claude/projects")
    files = sorted(glob.glob(os.path.join(root, "*", "*.jsonl")), key=os.path.getmtime, reverse=True)
    return files[:limit] if limit else files
