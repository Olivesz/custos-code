"""Live path for Claude Code (class H): hook handlers and the auto-mode loop state.

PostToolUse appends CALL/RESULT events to ~/.receipts/live/<session_id>.jsonl (the harness calls
us; the model has no write path). Stop reads `last_assistant_message` from the payload (the
transcript can lag), builds the ledger from the live file when present and the transcript
otherwise, runs claims -> verdicts, writes the receipt, and in auto mode returns a block decision
with deterministic nudges until the contract holds or the pass cap is hit.

Loop state per session: ~/.receipts/state/<session_id>.json {passes, nudge_seq, open: {claim_text: verdict}}.

Owner: Oliver.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import tomllib
from datetime import UTC, datetime
from typing import Any

from . import claims as claims_mod
from . import feedback, parsers, rerun
from . import judge as judge_mod
from . import review as review_mod
from . import verdicts as verdicts_mod
from .adapters import claude_code
from .ledger import MAX_OUTPUT_BYTES, chain, redact
from .models import Claim, EventFlags, EventKind, LedgerEvent, Session, Verdict, VerdictRecord

HOME = os.path.expanduser("~/.receipts")


def _config() -> dict[str, Any]:
    """Auto-mode settings, from ~/.receipts/config.toml with a per-invocation env override.

    `auto` blocks the agent's turn, so it must be opt-in and it must be possible to opt in for one
    project without arming every session on the machine. config.toml is global; the hook command in
    a project's own .claude/settings.json can set RECEIPTS_AUTO=1 instead, which scopes blocking to
    that project. RECEIPTS_AUTO=0 force-disables even when the global config enables it, so a repo
    can opt out of a machine-wide default.
    """
    cfg: dict[str, Any] = {"auto": False, "auto_max_passes": 3, "auto_clear": ["contradicted", "unrecorded", "unwitnessed"]}
    p = os.path.join(HOME, "config.toml")
    if os.path.exists(p):
        with open(p, "rb") as fh:
            data = tomllib.load(fh)
        cfg.update(data.get("tiers", {}))
    env = os.environ.get("RECEIPTS_AUTO")
    if env is not None:
        cfg["auto"] = env.strip().lower() in ("1", "true", "yes", "on")
    if (mp := os.environ.get("RECEIPTS_AUTO_MAX_PASSES")) and mp.isdigit():
        cfg["auto_max_passes"] = int(mp)
    return cfg


def _paths(session_id: str) -> tuple[str, str, str]:
    os.makedirs(os.path.join(HOME, "live"), exist_ok=True)
    os.makedirs(os.path.join(HOME, "state"), exist_ok=True)
    os.makedirs(os.path.join(HOME, "receipts"), exist_ok=True)
    return (os.path.join(HOME, "live", f"{session_id}.jsonl"),
            os.path.join(HOME, "state", f"{session_id}.json"),
            os.path.join(HOME, "receipts", f"{session_id}.txt"))


def _rc_dir() -> str:
    d = os.path.join(HOME, "rc")
    os.makedirs(d, exist_ok=True)
    return d


def _rc_pending_path(session_id: str) -> str:
    os.makedirs(os.path.join(HOME, "rc_pending"), exist_ok=True)
    return os.path.join(HOME, "rc_pending", f"{session_id}.json")


def _load_rc_pending(session_id: str) -> dict[str, str]:
    p = _rc_pending_path(session_id)
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as fh:
        data: dict[str, str] = json.load(fh)
    return data


def _save_rc_pending(session_id: str, pending: dict[str, str]) -> None:
    with open(_rc_pending_path(session_id), "w", encoding="utf-8") as fh:
        json.dump(pending, fh)


# ---------- PreToolUse ----------
def on_pre_tool_use(payload: dict[str, Any]) -> dict[str, Any] | None:
    """E5/E9: wrap a known-runner Bash command so `on_post_tool_use` can see the real resolved
    binary path and exit code, defeating a `./pytest` wrapper shadowing the real one. Returns the
    hook response JSON to print, or None to leave the command untouched.

    Issue #22: the trailer is written to a random per-call file under `~/.receipts/rc/` instead
    of stdout (the E9 experiment showed a stdout trailer is visible in the model's own tool
    output, letting it later forge the marker). `PostToolUse` cannot safely re-derive that random
    path by re-parsing `tool_input.command` (undocumented whether it sees the original or the
    rewritten command), so the path is instead handed across via a small per-session pending map
    keyed on `tool_use_id`, which both events document. No `tool_use_id` means no way to
    correlate the two sides, so the command is left unwrapped rather than leaking an orphan file.
    """
    if payload.get("tool_name") != "Bash":
        return None
    inp = payload.get("tool_input")
    command = inp.get("command") if isinstance(inp, dict) else None
    if not isinstance(command, str):
        return None
    tool_use_id = payload.get("tool_use_id")
    if not isinstance(tool_use_id, str):
        return None
    rc_path = os.path.join(_rc_dir(), secrets.token_hex(16))
    wrapped = parsers.wrap_command_for_resolution(command, rc_path=rc_path)
    if wrapped is None:
        return None
    sid = str(payload.get("session_id", "unknown"))
    pending = _load_rc_pending(sid)
    pending[tool_use_id] = rc_path
    _save_rc_pending(sid, pending)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"command": wrapped}}}


# ---------- PostToolUse ----------
def on_post_tool_use(payload: dict[str, Any]) -> None:
    sid = str(payload.get("session_id", "unknown"))
    live, _, _ = _paths(sid)
    tool = str(payload.get("tool_name", ""))
    raw_inp = payload.get("tool_input")
    inp: dict[str, Any] = dict(raw_inp) if isinstance(raw_inp, dict) else {}
    resp = payload.get("tool_response")
    text = resp if isinstance(resp, str) else json.dumps(resp) if resp is not None else ""
    # Issue #22: `on_pre_tool_use` (when it wrapped this call) writes the resolved binary path and
    # exit code to a per-call file instead of stdout, so there is no trailer in `text` to strip
    # here -- look the file up via the tool_use_id pending map instead.
    resolved_bin: str | None = None
    wrapped_exit_code: int | None = None
    tool_use_id = payload.get("tool_use_id")
    if isinstance(tool_use_id, str):
        pending = _load_rc_pending(sid)
        rc_path = pending.pop(tool_use_id, None)
        if rc_path is not None:
            resolved_bin, wrapped_exit_code = parsers.read_rc_file(rc_path)
            if os.path.exists(rc_path):
                os.remove(rc_path)
            _save_rc_pending(sid, pending)
    text = redact(text)
    if resolved_bin is not None:
        inp["resolved_bin"] = resolved_bin  # feeds a future rules.py trust check (E5); unused today
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    side = bool(payload.get("agent_id"))
    n = sum(1 for _ in open(live)) if os.path.exists(live) else 0
    ts = datetime.now(UTC)
    call = LedgerEvent(seq=n, ts=ts, session_id=sid, kind=EventKind.CALL, tool=tool, input=redact(dict(inp)),
                       paths=claude_code._paths_from_input(tool, dict(inp), cwd), cwd=cwd, flags=EventFlags(sidechain=side))
    flags = EventFlags(sidechain=side)
    cmd = inp.get("command")
    if tool == "Bash" and isinstance(cmd, str) and claude_code._PIPE_RE.search(cmd):
        flags.piped = True
    if len(text.encode()) > MAX_OUTPUT_BYTES:
        flags.truncated = True
    res = LedgerEvent(seq=n + 1, ts=ts, session_id=sid, kind=EventKind.RESULT, tool=tool,
                      output=text.encode()[:MAX_OUTPUT_BYTES].decode(errors="ignore"),
                      output_hash=hashlib.sha256(text.encode()).hexdigest(), cwd=cwd, flags=flags,
                      exit_code=wrapped_exit_code,  # E9: only set when on_pre_tool_use wrapped this command
                      paths=[str(inp["file_path"])] if isinstance(inp.get("file_path"), str) else [])
    with open(live, "a", encoding="utf-8") as fh:
        fh.write(call.model_dump_json() + "\n")
        fh.write(res.model_dump_json() + "\n")


# ---------- ledger assembly for Stop ----------
def _ledger_for(payload: dict[str, Any]) -> tuple[Session, list[LedgerEvent]]:
    sid = str(payload.get("session_id", "unknown"))
    live, _, _ = _paths(sid)
    tpath = payload.get("transcript_path")
    if isinstance(tpath, str) and os.path.exists(tpath):
        sess, ledger, _ = claude_code.parse(tpath)
        if ledger or not os.path.exists(live):
            return sess, ledger
    events: list[LedgerEvent] = []
    if os.path.exists(live):
        with open(live, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    events.append(LedgerEvent.model_validate_json(line))
    events = chain(events)
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    sess = Session(id=sid, source="claude_code", agent="claude-code", cwd=cwd, n_events=len(events),
                   ledger_root_hash=events[-1].hash if events else "")
    return sess, events


# ---------- Stop ----------
def _render(claims: list[Claim], recs: list[VerdictRecord]) -> str:
    mark = {Verdict.CONFIRMED: "✓", Verdict.CONTRADICTED: "✗", Verdict.UNWITNESSED: "?", Verdict.UNRECORDED: "○", Verdict.QUALIFIED: "≈"}
    by = {c.id: c for c in claims}
    lines = []
    for r in recs:
        c = by[r.claim_id]
        ev = " ".join(f"#{e}" for e in r.evidence) or "—"
        lines.append(f"{mark[r.verdict]} {r.verdict.value:<12} {c.text}\n    tier {r.tier} · {r.method} · {ev} · {r.rationale}" + (f" · {r.qualifier}" if r.qualifier else ""))
    s = verdicts_mod.summary(recs)
    lines.append("receipts · " + " · ".join(f"{s[v.value]} {mark[v]}" for v in Verdict if s[v.value]))
    return "\n".join(lines)


def on_stop(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Returns the JSON to print on stdout (block decision), or None for exit 0 with no output."""
    sid = str(payload.get("session_id", "unknown"))
    report = payload.get("last_assistant_message")
    if not isinstance(report, str) or not report.strip():
        return None
    _, state_p, receipt_p = _paths(sid)
    sess, ledger = _ledger_for(payload)
    repo = payload.get("cwd") if isinstance(payload.get("cwd"), str) else sess.cwd
    # The measured path (eval/arms/RESULTS.md: 86% vs 70% for the tiered pipeline, McNemar
    # p=0.00017). Falls back to deterministic rules with no key, so the hook never hard-fails.
    backend = judge_mod.make_backend()
    if backend is not None:
        out = review_mod.review(report, ledger, sid, backend)
        claims, recs = out.claims, out.verdicts
    else:
        claims = claims_mod.extract(report, sid)
        recs = verdicts_mod.run(claims, ledger, repo)
    with open(receipt_p, "w", encoding="utf-8") as fh:
        fh.write(_render(claims, recs) + "\n")

    cfg = _config()
    if not cfg.get("auto"):
        return None
    clear = set(cfg.get("auto_clear", []))
    by = {c.id: c for c in claims}
    open_pairs = [(by[r.claim_id], r) for r in recs if r.verdict.value in clear]
    state: dict[str, Any] = {"passes": 0, "nudge_seq": -1}
    if os.path.exists(state_p):
        with open(state_p, encoding="utf-8") as fh:
            state = json.load(fh)
    if bool(payload.get("stop_hook_active")) and "nudge_seq" in state:
        # a continuation: a previously open claim clears only on evidence newer than the nudge (docs/DESIGN.md §6).
        # A reworded claim that now "confirms" on old evidence stays open as unwitnessed.
        nudge_seq = int(state.get("nudge_seq", -1))
        prev_open = set(state.get("open", []))
        for r in recs:
            c = by[r.claim_id]
            if c.text in prev_open and r.verdict in (Verdict.CONFIRMED, Verdict.QUALIFIED) and not feedback.cleared(r, r, ledger, nudge_seq):
                r.verdict = Verdict.UNWITNESSED
                r.rationale = "Reworded, but no tool call after the nudge bears on this claim; it is not cleared."
                open_pairs.append((c, r))
    if not open_pairs:
        if os.path.exists(state_p):
            os.remove(state_p)
        return None
    passes = int(state.get("passes", 0)) + 1
    max_passes = int(cfg.get("auto_max_passes", 3))
    if max_passes and passes > max_passes:
        os.remove(state_p) if os.path.exists(state_p) else None
        return None  # cap hit: hand back to the human with the receipt file
    nudge_seq = max((e.seq for e in ledger), default=-1)
    with open(state_p, "w", encoding="utf-8") as fh:
        json.dump({"passes": passes, "nudge_seq": nudge_seq, "open": [c.text for c, _ in open_pairs]}, fh)
    reason = feedback.build_block_reason(open_pairs, ledger, passes, max_passes)
    return {"decision": "block", "reason": reason}


def _load_env_file() -> None:
    """Load ~/.receipts/env into the environment for keys the hook shell does not inherit.

    Claude Code runs hooks in a non-login, non-interactive shell, so exports from .zshrc or a
    profile are not present. Without a key, `judge.make_backend()` returns None and the Stop hook
    silently falls back to the deterministic rules -- a quieter, measurably worse receipt (70% vs
    92%) with no indication that it happened. This is the difference between a working install and
    one that looks like it works.

    Deliberately NOT a repo-level .env: the file lives under ~/.receipts so it cannot be committed
    by accident. Existing environment variables always win, so CI and explicit exports override it.
    Format is KEY=VALUE, one per line, `#` comments and surrounding quotes allowed.
    """
    p = os.path.join(HOME, "env")
    if not os.path.exists(p):
        return
    try:
        with open(p, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip().removeprefix("export ").strip()
                if k and k not in os.environ:
                    os.environ[k] = v.strip().strip("'\"")
    except OSError:
        return  # unreadable key file is not a reason to fail a hook


def main(event: str, session_id: str | None = None, claim_id: str | None = None) -> int:
    _load_env_file()
    if event == "rerun-worker":
        # E4: a detached subprocess `rerun.spawn_async` launched directly -- no hook payload,
        # no stdin to read; its identity is these two args.
        if not session_id or not claim_id:
            return 2
        rerun.run_worker(session_id, claim_id)
        return 0
    # Everything below fails OPEN. `hooks/*.sh` append `|| true`, but the command that
    # `receipts watch --install` writes into settings.json invokes this binary directly, with no
    # wrapper to swallow anything -- so an unhandled exception here surfaces as a traceback and a
    # non-zero exit from a Claude Code hook. For Stop that reads as "block", which would be a
    # contradiction backed by no evidence at all; for PostToolUse it means the ledger write is
    # skipped, and a missing ledger silently degrades every later verdict to `unwitnessed`.
    # A checker that cannot run is not evidence about the agent. Say so on stderr, exit 0.
    try:
        payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        print(f"receipts: unreadable hook payload ({type(e).__name__}); not blocking.", file=sys.stderr)
        return 0
    if not isinstance(payload, dict):
        print("receipts: hook payload was not a JSON object; not blocking.", file=sys.stderr)
        return 0
    try:
        if event == "pre":
            out = on_pre_tool_use(payload)
            if out is not None:
                print(json.dumps(out))
            return 0
        if event == "post-tool-use":
            on_post_tool_use(payload)
            return 0
        if event == "stop":
            out = on_stop(payload)
            if out is not None:
                print(json.dumps(out))
            return 0
    except Exception as e:  # noqa: BLE001 - a hook must not take the turn down with it
        print(f"receipts: {event} hook failed ({type(e).__name__}: {e}); not blocking.", file=sys.stderr)
        return 0
    return 2
