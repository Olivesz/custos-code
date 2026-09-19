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
import sys
import tomllib
from datetime import UTC, datetime
from typing import Any

from . import claims as claims_mod
from . import feedback
from . import verdicts as verdicts_mod
from .adapters import claude_code
from .ledger import MAX_OUTPUT_BYTES, chain, redact
from .models import Claim, EventFlags, EventKind, LedgerEvent, Session, Verdict, VerdictRecord

HOME = os.path.expanduser("~/.receipts")


def _config() -> dict[str, Any]:
    cfg: dict[str, Any] = {"auto": False, "auto_max_passes": 3, "auto_clear": ["contradicted", "unrecorded", "unwitnessed"]}
    p = os.path.join(HOME, "config.toml")
    if os.path.exists(p):
        with open(p, "rb") as fh:
            data = tomllib.load(fh)
        cfg.update(data.get("tiers", {}))
    return cfg


def _paths(session_id: str) -> tuple[str, str, str]:
    os.makedirs(os.path.join(HOME, "live"), exist_ok=True)
    os.makedirs(os.path.join(HOME, "state"), exist_ok=True)
    os.makedirs(os.path.join(HOME, "receipts"), exist_ok=True)
    return (os.path.join(HOME, "live", f"{session_id}.jsonl"),
            os.path.join(HOME, "state", f"{session_id}.json"),
            os.path.join(HOME, "receipts", f"{session_id}.txt"))


# ---------- PostToolUse ----------
def on_post_tool_use(payload: dict[str, Any]) -> None:
    sid = str(payload.get("session_id", "unknown"))
    live, _, _ = _paths(sid)
    tool = str(payload.get("tool_name", ""))
    raw_inp = payload.get("tool_input")
    inp: dict[str, Any] = dict(raw_inp) if isinstance(raw_inp, dict) else {}
    resp = payload.get("tool_response")
    text = resp if isinstance(resp, str) else json.dumps(resp) if resp is not None else ""
    text = redact(text)
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


def main(event: str) -> int:
    payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    if event == "post-tool-use":
        on_post_tool_use(payload)
        return 0
    if event == "stop":
        out = on_stop(payload)
        if out is not None:
            print(json.dumps(out))
        return 0
    return 2
