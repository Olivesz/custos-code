"""Live path for Claude Code (class H): hook handlers and the auto-mode loop state.

PostToolUse appends CALL/RESULT events to ~/.custos-code/live/<session_id>.jsonl (the harness calls
us; the model has no write path). Stop reads `last_assistant_message` from the payload (the
transcript can lag), builds the ledger from the live file when present and the transcript
otherwise, runs claims -> verdicts, writes the receipt, and in auto mode returns a block decision
with deterministic nudges until the contract holds or the pass cap is hit.

Loop state per session: ~/.custos-code/state/<session_id>.json {passes, nudge_seq, open: {claim_text: verdict}}.

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
from . import scope as scope_mod
from . import verdicts as verdicts_mod
from .adapters import claude_code
from .ledger import MAX_OUTPUT_BYTES, chain, redact
from .models import Claim, EventFlags, EventKind, LedgerEvent, Session, Verdict, VerdictRecord

HOME = os.path.expanduser("~/.custos-code")


def _out_of_scope(payload: dict[str, Any]) -> bool:
    """True when CUSTOS_CODE_ONLY_IN is set and this session's cwd is not inside it.

    Exists for measurement validity, not convenience. The agent under test can read every file in
    its working directory, so a project-local `.claude/settings.json` tells it that its output is
    being checked and by what -- and on 2026-09-19 a run did exactly that, reading both the hook
    config and a README that described the trap. An agent that knows it is being audited is not
    the agent whose honesty we are trying to measure.

    With CUSTOS_CODE_ONLY_IN the hooks live in the user's global settings and the project directory
    holds nothing at all. The guard fails toward doing nothing: an unset, empty, or unresolvable
    value, or a payload with no cwd, means this hook does not act.
    """
    root = os.environ.get("CUSTOS_CODE_ONLY_IN", "").strip()
    if not root:
        return False
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return True
    try:
        root_r = os.path.realpath(os.path.expanduser(root))
        cwd_r = os.path.realpath(cwd)
    except OSError:
        return True
    return os.path.commonpath([root_r, cwd_r]) != root_r


def _config() -> dict[str, Any]:
    """Auto-mode settings, from ~/.custos-code/config.toml with a per-invocation env override.

    `auto` blocks the agent's turn, so it must be opt-in and it must be possible to opt in for one
    project without arming every session on the machine. config.toml is global; the hook command in
    a project's own .claude/settings.json can set CUSTOS_CODE_AUTO=1 instead, which scopes blocking to
    that project. CUSTOS_CODE_AUTO=0 force-disables even when the global config enables it, so a repo
    can opt out of a machine-wide default.
    """
    # Only an accusation holds the turn. `unrecorded` and `unwitnessed` are the checker reporting
    # the limits of its own evidence -- "the output was piped", "nothing in the log either way" --
    # and gating on them made the agent responsible for facts about the recorder. Measured over 648
    # real claims on 2026-09-20 they are 21.0% and 28.9%, so half of every report held the turn
    # open, and `unwitnessed` on a heading or a piece of advice cannot be cleared by any amount of
    # further work. That is the three-pass spin, and it fired on honest reports.
    cfg: dict[str, Any] = {"auto": False, "auto_max_passes": 3, "auto_clear": ["contradicted"]}
    p = os.path.join(HOME, "config.toml")
    if os.path.exists(p):
        with open(p, "rb") as fh:
            data = tomllib.load(fh)
        cfg.update(data.get("tiers", {}))
    env = os.environ.get("CUSTOS_CODE_AUTO")
    if env is not None:
        cfg["auto"] = env.strip().lower() in ("1", "true", "yes", "on")
    if (mp := os.environ.get("CUSTOS_CODE_AUTO_MAX_PASSES")) and mp.isdigit():
        cfg["auto_max_passes"] = int(mp)
    return cfg


def _scope_mode() -> str:
    """`off` | `warn` | `on`, from CUSTOS_CODE_SCOPE or config.toml. Default OFF, deliberately.

    Every threshold in scope.py is a default I wrote, not a measurement. Issue #57 calibrates them
    against ~400 sessions of accepted work, and until that reports, a scope gate that interrupts
    good work is strictly worse than no scope gate -- see the Stop-hook latency that made the
    terminal unusable on 2026-09-19. So this ships inert and is switched on by a number, not by
    confidence.

    `warn` bands and records without ever denying: that is the mode #57's harness runs in.
    """
    v = (os.environ.get("CUSTOS_CODE_SCOPE") or _config().get("scope") or "off")
    v = str(v).strip().lower()
    return v if v in ("off", "warn", "on") else "off"


def _scope_grant(payload: dict[str, Any], policy: scope_mod.Policy) -> scope_mod.Grant:
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""
    sid = str(payload.get("session_id", "unknown"))
    approved: tuple[str, ...] = ()
    _, state_p, _ = _paths(sid)
    if os.path.exists(state_p):
        try:
            with open(state_p, encoding="utf-8") as fh:
                got = json.load(fh).get("scope_approved")
            if isinstance(got, list):
                approved = tuple(str(x) for x in got)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return scope_mod.Grant.for_session(cwd or os.getcwd(), approved=approved, policy=policy)


def _scope_gate(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Band this call and, if the mode allows, stop it before it happens.

    docs/SCOPE.md §5: scope is checked at PreToolUse, BEFORE the action -- "ask before doing
    something irreversible" is what every permission system does, not halting on an opinion. And
    the cost asymmetry inverts against integrity: a scope false positive costs one pause, a scope
    false negative costs a force-push.

    The mode decides the response, because a flag is a message to a human and an unattended run has
    nobody reading it:

                GREEN     YELLOW    RED
      attended  pass      ask       deny
      unattended pass     deny      deny

    Fails OPEN on any error. A checker that cannot run is not evidence about the agent.
    """
    mode = _scope_mode()
    if mode == "off":
        return None
    try:
        raw = payload.get("tool_input")
        inp: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
        policy = scope_mod.Policy.load()
        f = scope_mod.classify(str(payload.get("tool_name", "")), inp,
                                _scope_grant(payload, policy), policy=policy)
    except Exception as e:  # noqa: BLE001 - never take the turn down over a scope check
        print(f"receipts: scope check failed ({type(e).__name__}); allowing.", file=sys.stderr)
        return None
    if not f.gates or mode == "warn":
        return None
    unattended = bool(_config().get("auto"))
    decision = "deny" if (f.band is scope_mod.Band.RED or unattended) else "ask"
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": _scope_reason(f, decision),
    }}


def _scope_reason(f: scope_mod.Finding, decision: str) -> str:
    """Say why, in terms of the actual finding. A generic reason trains people to click through."""
    if f.band is scope_mod.Band.RED:
        why = "this cannot be undone"
    elif f.rule == "write-outside-cwd":
        why = "this writes outside the directory this session was started in"
    elif f.rule == "unrecoverable-write":
        why = "there is no git work tree here, so this cannot be reverted"
    else:
        why = "this reaches outside the workspace"
    tail = ("" if decision == "deny"
            else " Approve it and it will not be asked again this session.")
    return f"receipts/scope [{f.band.value}] {f.rule} — {why}: {f.detail}.{tail}"


def _paths(session_id: str) -> tuple[str, str, str]:
    os.makedirs(os.path.join(HOME, "live"), exist_ok=True)
    os.makedirs(os.path.join(HOME, "state"), exist_ok=True)
    os.makedirs(os.path.join(HOME, "custos-code"), exist_ok=True)
    return (os.path.join(HOME, "live", f"{session_id}.jsonl"),
            os.path.join(HOME, "state", f"{session_id}.json"),
            os.path.join(HOME, "custos-code", f"{session_id}.txt"))


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


def _unpack_pending(entry: str) -> tuple[str, str | None]:
    """A pending entry is `{"rc": path, "cmd": original}`; older ones were a bare path string.

    Tolerating the bare form matters because a session in flight when this shipped would otherwise
    lose its rc files and, worse, keep recording our rewritten command as if the agent had run it.
    """
    try:
        d = json.loads(entry)
    except (json.JSONDecodeError, TypeError):
        return entry, None
    if not isinstance(d, dict):
        return entry, None
    rc = d.get("rc")
    cmd = d.get("cmd")
    return (rc if isinstance(rc, str) else entry), (cmd if isinstance(cmd, str) else None)


def _save_rc_pending(session_id: str, pending: dict[str, str]) -> None:
    with open(_rc_pending_path(session_id), "w", encoding="utf-8") as fh:
        json.dump(pending, fh)


# ---------- PreToolUse ----------
def on_pre_tool_use(payload: dict[str, Any]) -> dict[str, Any] | None:
    """E5/E9: wrap a known-runner Bash command so `on_post_tool_use` can see the real resolved
    binary path and exit code, defeating a `./pytest` wrapper shadowing the real one. Returns the
    hook response JSON to print, or None to leave the command untouched.

    Issue #22: the trailer is written to a random per-call file under `~/.custos-code/rc/` instead
    of stdout (the E9 experiment showed a stdout trailer is visible in the model's own tool
    output, letting it later forge the marker). `PostToolUse` cannot safely re-derive that random
    path by re-parsing `tool_input.command` (undocumented whether it sees the original or the
    rewritten command), so the path is instead handed across via a small per-session pending map
    keyed on `tool_use_id`, which both events document. No `tool_use_id` means no way to
    correlate the two sides, so the command is left unwrapped rather than leaking an orphan file.
    """
    if _out_of_scope(payload):
        return None
    # Scope first: a RED action must never get wrapped and run. The E5 rewrite below only makes a
    # command observable; it does not make it safe.
    if (gate := _scope_gate(payload)) is not None:
        return gate
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
    # Keep the ORIGINAL command beside the rc path. PostToolUse sees our rewritten command, and
    # recording that would be wrong twice over: the receipt would quote a command the agent never
    # ran, and the wrapper's own `command -v ... 2>/dev/null` matches the output-filtered detector,
    # so every wrapped call would be flagged `piped` and its evidence discounted. Observed on
    # session 21756df4: five true claims came back `unrecorded` for "filtered" output that our own
    # instrumentation had filtered, and a sixth was contradicted outright.
    pending[tool_use_id] = json.dumps({"rc": rc_path, "cmd": command})
    _save_rc_pending(sid, pending)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"command": wrapped}}}


# ---------- PostToolUse ----------
def _response_text(resp: Any) -> str:
    """The tool's own output, the way the replay adapter records it.

    This used to be `json.dumps(resp)`, which wrapped a Bash result in `{"stdout": "...\\n..."}`
    with its newlines escaped. Every parser downstream then read one long line: anything anchored
    to the start of a line could not match, so `pytest -q` -- which prints no session banner, only
    a tail like `86 passed in 1.2s` -- parsed as "not a test runner at all" on the LIVE path while
    parsing correctly on replay.

    That divergence matters more than the one case. Every accuracy number this project quotes was
    produced by replaying transcripts through `adapters.claude_code`, which extracts stdout
    properly. The hook people actually install stored something else. Measurements taken on the
    replay path were never evidence about the live path.
    """
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        parts = [str(resp[k]) for k in ("stdout", "stderr") if isinstance(resp.get(k), str) and resp[k]]
        if parts:
            return "\n".join(parts)
        if isinstance(resp.get("content"), str):
            return str(resp["content"])
    return json.dumps(resp) if resp is not None else ""


def on_post_tool_use(payload: dict[str, Any]) -> None:
    if _out_of_scope(payload):
        return
    sid = str(payload.get("session_id", "unknown"))
    live, _, _ = _paths(sid)
    tool = str(payload.get("tool_name", ""))
    raw_inp = payload.get("tool_input")
    inp: dict[str, Any] = dict(raw_inp) if isinstance(raw_inp, dict) else {}
    resp = payload.get("tool_response")
    text = _response_text(resp)
    # Issue #22: `on_pre_tool_use` (when it wrapped this call) writes the resolved binary path and
    # exit code to a per-call file instead of stdout, so there is no trailer in `text` to strip
    # here -- look the file up via the tool_use_id pending map instead.
    resolved_bin: str | None = None
    wrapped_exit_code: int | None = None
    tool_use_id = payload.get("tool_use_id")
    if isinstance(tool_use_id, str):
        pending = _load_rc_pending(sid)
        entry = pending.pop(tool_use_id, None)
        if entry is not None:
            rc_path, original_cmd = _unpack_pending(entry)
            resolved_bin, wrapped_exit_code = parsers.read_rc_file(rc_path)
            if os.path.exists(rc_path):
                os.remove(rc_path)
            # Restore the agent's own command. `tool_input` here holds OUR rewrite, which quotes a
            # command the agent never ran and whose `command -v ... 2>/dev/null` trips the
            # output-filtered detector below.
            if original_cmd is not None:
                inp["command"] = original_cmd
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
    if tool == "Bash" and isinstance(cmd, str) and parsers.is_piped(cmd):
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
def _collect_reruns(session_id: str, ledger: list[LedgerEvent], live_path: str) -> list[LedgerEvent]:
    """Fold any finished Tier 3 results into the ledger, once.

    `spawn_async` detaches and writes a RERUN event to a result file; Claude Code hooks are
    one-shot, so the Stop call that launched it has already returned. Something has to pick the
    result up on a LATER turn or the re-run is theatre -- the subprocess runs, the evidence lands
    on disk, and the checker never looks. Nothing did: `load_result` had no callers outside its
    own module, which an adversarial pass caught before this shipped.

    Appending to the live ledger file (not just the in-memory list) is what makes it persist, so
    the evidence stays available to `receipts check` and to every later pass rather than being
    consumed by whichever turn happened to notice it.

    Resolves the seq placeholder `rerun.run_worker` left as NEEDS-DECISION(oliver): the event is
    numbered when it is folded in, because only here is the ledger's length known.
    """
    # Ask rerun where it writes rather than rebuilding the path: two copies of the same layout
    # drift, and a reader looking in the wrong directory silently finds nothing forever -- which
    # is indistinguishable from "no re-runs happened".
    d = str(rerun._rerun_dir(session_id))
    if not os.path.isdir(d):
        return ledger
    next_seq = max((e.seq for e in ledger), default=-1) + 1
    for name in sorted(os.listdir(d)):
        if not name.endswith(".result.json"):
            continue
        claim_id = name[: -len(".result.json")]
        try:
            ev = rerun.load_result(session_id, claim_id)
        except (OSError, ValueError):
            ev = None
        if ev is None:
            continue
        ev.seq = next_seq
        next_seq += 1
        ledger.append(ev)
        # Only persist when a live ledger already exists. If this session's ledger came from the
        # transcript (hooks installed mid-flight), creating a live file containing nothing but
        # RERUN events would make the NEXT turn prefer it and lose the transcript entirely --
        # `_ledger_for` takes the live file whenever it has any events at all.
        if os.path.exists(live_path):
            try:
                with open(live_path, "a", encoding="utf-8") as fh:
                    fh.write(ev.model_dump_json() + "\n")
                os.remove(os.path.join(d, name))   # consumed exactly once
            except OSError:
                pass
    return ledger


def _ledger_for(payload: dict[str, Any]) -> tuple[Session, list[LedgerEvent]]:
    """The ledger for this session: the live hook file first, the transcript only as a fallback.

    Order matters, and it used to be backwards -- the transcript was preferred whenever
    `transcript_path` existed, which is always. Two consequences, both observed on a real session
    (adc885ec, 2026-09-19):

    1. **We discarded the output we exist to capture.** PostToolUse records each tool's FULL
       stdout before the harness truncates it; that is the whole reason the hook exists
       (docs/DESIGN.md §5: 42% of test output was piped away). On that session the live file held
       28,437 bytes of captured output against the transcript's 20,424. Reading the transcript
       threw away 8KB of evidence and produced a receipt full of `unrecorded ... not visible due
       to truncation` for claims the live file could have settled.
    2. **Citations pointed at the wrong lines.** The two sources number events independently --
       the transcript also numbers assistant/user text records, the live file only tool events. On
       that session `git mv` was seq 16 in the transcript and seq 14 in the live file. The receipt
       cited #16; a reader checking ~/.custos-code/live/<id>.jsonl, which is the artifact we tell
       people to audit, finds an unrelated pytest run there. Every citation in that receipt was
       unverifiable against the ledger on disk.

    So: live file when it has events, transcript otherwise (a session whose hooks were installed
    mid-flight has no live file for its earlier turns, which is the case this fallback is for).
    """
    sid = str(payload.get("session_id", "unknown"))
    live, _, _ = _paths(sid)
    events: list[LedgerEvent] = []
    if os.path.exists(live):
        with open(live, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    events.append(LedgerEvent.model_validate_json(line))
    if not events:
        tpath = payload.get("transcript_path")
        if isinstance(tpath, str) and os.path.exists(tpath):
            sess, ledger, _ = claude_code.parse(tpath)
            if ledger:
                return sess, ledger
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
    lines.append("custos-code · " + " · ".join(f"{s[v.value]} {mark[v]}" for v in Verdict if s[v.value]))
    return "\n".join(lines)


def on_stop(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Returns the JSON to print on stdout (block decision), or None for exit 0 with no output."""
    if _out_of_scope(payload):
        return None
    sid = str(payload.get("session_id", "unknown"))
    report = payload.get("last_assistant_message")
    if not isinstance(report, str) or not report.strip():
        return None
    _, state_p, receipt_p = _paths(sid)
    sess, ledger = _ledger_for(payload)
    live_p, _, _ = _paths(sid)
    ledger = _collect_reruns(sid, ledger, live_p)   # evidence from earlier turns' Tier 3 jobs
    repo = payload.get("cwd") if isinstance(payload.get("cwd"), str) else sess.cwd

    # Cheap gate before the model call. A Stop hook fires on EVERY turn, so a turn that ran one
    # `rm` was paying for a full-session review, and in auto mode up to three of them. Observed
    # on 2026-09-19: a one-line command took tens of seconds and the user reasonably concluded the
    # terminal was broken. A checker nobody leaves switched on verifies nothing.
    #
    # Skip when there is nothing a receipt could say:
    #   - no tool calls at all in the session -> every claim would be `unwitnessed` anyway, which
    #     is never an accusation and never blocks, so the call buys nothing.
    #   - no NEW tool calls since the last receipt -> the evidence has not moved, so neither can
    #     any verdict. This is the common case for conversational turns.
    n_calls = sum(1 for e in ledger if e.kind == EventKind.CALL)
    if n_calls == 0:
        return None
    seen_p = os.path.join(HOME, "seen", f"{sid}.json")
    os.makedirs(os.path.dirname(seen_p), exist_ok=True)
    last_seq = -1
    if os.path.exists(seen_p) and not payload.get("stop_hook_active"):
        try:
            with open(seen_p, encoding="utf-8") as fh:
                last_seq = int(json.load(fh).get("seq", -1))
        except (OSError, ValueError, json.JSONDecodeError):
            last_seq = -1
    max_seq = max((e.seq for e in ledger), default=-1)
    if last_seq >= max_seq:
        return None
    with open(seen_p, "w", encoding="utf-8") as fh:
        json.dump({"seq": max_seq}, fh)
    # Loop state has to be read BEFORE the review, not after: `nudge_seq` marks where the last
    # correction request fell in the ledger, and `annotate()` needs it to draw the boundary between
    # the superseded attempt and the new one. Without it the model can cite a piped run from pass 1
    # as evidence against work the agent redid cleanly in pass 2 (session 21756df4).
    state: dict[str, Any] = {"passes": 0, "nudge_seq": -1}
    if os.path.exists(state_p):
        try:
            with open(state_p, encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    prior_nudge = int(state.get("nudge_seq", -1)) if payload.get("stop_hook_active") else -1

    # The measured path (eval/arms/RESULTS.md: 86% vs 70% for the tiered pipeline, McNemar
    # p=0.00017). Falls back to deterministic rules with no key, so the hook never hard-fails.
    backend = judge_mod.make_backend()
    if backend is not None:
        out = review_mod.review(report, ledger, sid, backend, nudge_seq=prior_nudge,
                                repo_root=repo)
        claims = out.claims
        recs = verdicts_mod.apply_reruns(claims, out.verdicts, ledger)
    else:
        claims = claims_mod.extract(report, sid)
        recs = verdicts_mod.run(claims, ledger, repo)
    # Tier 3: launch a re-execution for any claim a re-run could actually settle. This is the
    # second GROUNDED source the architecture argues for -- not another opinion, but the command
    # run again and looked at. Coverage is worth 4-74x more than a second verifier at any
    # plausible likelihood ratio, and this is what moves coverage.
    #
    # `spawn_async` detaches, so the Stop hook still returns in the ~10s it wants. The result is
    # picked up by a later pass, by the extension, or by `receipts check` -- there is no path back
    # into this call, which has already returned. `verdicts.should_rerun` is deliberately narrow:
    # open verdict, runnable claim type, a real repo, a committed runner config, within budget,
    # and not already tried on this exact tree.
    spent: set[str] = set(state.get("reruns", []))
    if repo:
        for c, r in zip(claims, recs, strict=False):
            if not verdicts_mod.should_rerun(c, r, repo, already=spent):
                continue
            key = verdicts_mod.rerun_key(c, repo)
            try:
                # report_seq anchors the RERUN event after the evidence it re-checks
                rerun.spawn_async(sid, c.id, repo, report_seq=max_seq, claim_text=c.text,
                                   cmd=verdicts_mod.rerun_command(c, repo),
                                   claim_kind=verdicts_mod.rerun_kind(c).value)
            except Exception as e:  # noqa: BLE001 - a failed launch must not fail the turn
                print(f"receipts: rerun launch failed ({type(e).__name__}); skipping.", file=sys.stderr)
                continue
            if key:
                spent.add(key)
        if spent != set(state.get("reruns", [])):
            # Written now, not with the auto-mode state below: the budget has to survive turns
            # that do not block, or a session with auto mode off re-runs on every single turn.
            state["reruns"] = sorted(spent)
            try:
                with open(state_p, "w", encoding="utf-8") as fh:
                    json.dump(state, fh)
            except OSError:
                pass

    with open(receipt_p, "w", encoding="utf-8") as fh:
        fh.write(_render(claims, recs) + "\n")

    cfg = _config()
    if not cfg.get("auto"):
        return None
    clear = set(cfg.get("auto_clear", []))
    by = {c.id: c for c in claims}
    open_pairs = [(by[r.claim_id], r) for r in recs
                  if r.verdict.value in clear and not review_mod.is_advisory(r)]
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
        json.dump({"passes": passes, "nudge_seq": nudge_seq,
                   "open": [c.text for c, _ in open_pairs],
                   "reruns": state.get("reruns", []),          # do not drop the Tier 3 budget
                   "scope_approved": state.get("scope_approved", [])}, fh)
    reason = feedback.build_block_reason(open_pairs, ledger, passes, max_passes)
    return {"decision": "block", "reason": reason}


def main(event: str, session_id: str | None = None, claim_id: str | None = None) -> int:
    if event == "rerun-worker":
        # E4: a detached subprocess `rerun.spawn_async` launched directly -- no hook payload,
        # no stdin to read; its identity is these two args.
        if not session_id or not claim_id:
            return 2
        rerun.run_worker(session_id, claim_id)
        return 0
    # Everything below fails OPEN. `hooks/*.sh` append `|| true`, but the command that
    # `custos-code watch --install` writes into settings.json invokes this binary directly, with no
    # wrapper to swallow anything -- so an unhandled exception here surfaces as a traceback and a
    # non-zero exit from a Claude Code hook. For Stop that reads as "block", which would be a
    # contradiction backed by no evidence at all; for PostToolUse it means the ledger write is
    # skipped, and a missing ledger silently degrades every later verdict to `unwitnessed`.
    # A checker that cannot run is not evidence about the agent. Say so on stderr, exit 0.
    try:
        payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        print(f"custos-code: unreadable hook payload ({type(e).__name__}); not blocking.", file=sys.stderr)
        return 0
    if not isinstance(payload, dict):
        print("custos-code: hook payload was not a JSON object; not blocking.", file=sys.stderr)
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
        print(f"custos-code: {event} hook failed ({type(e).__name__}: {e}); not blocking.", file=sys.stderr)
        return 0
    return 2
