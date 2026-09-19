"""Class M: the recorder that needs no harness at all (docs/ADAPTERS.md §4).

The floor of the coverage argument: with no hooks and no session file, a shell trap still knows
every command, its exit status, its cwd and its pid chain, and `git reflog` still knows every ref
move. `install_snippet` emits the bash/zsh lines `receipts record` writes into the user's rc file;
this module parses what they log.

Wire format, one JSON object per line in ~/.receipts/machine/<host>-<date>.jsonl:
  {"recorder":"receipts-machine","v":1,"event":"start|end|fs|git","ts":<epoch float>,
   "cmd":"pytest -q","cwd":"/abs","tty":"/dev/ttys004","pid":123,"ppid":99,"ppid_chain":["bash","codex"],
   "exit":0,"dur_ms":2250,"out":"<optional captured output>",
   "path":"src/a.py","op":"modified",              # event=fs
   "ref":"HEAD","from":"abc","to":"def","subject":"..."}  # event=git

Attribution: class M cannot prove which agent ran a command, so `ppid_chain` is recorded as a hint
and nothing more. When a class-H/F adapter also covered the session its events win, and machine
rows only fill exit-code gaps (see `merge_exit_codes`); alone, they support outcome claims but
leave tool attribution `unwitnessed` rather than guessing from timing.

Owner: Ananya.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
from datetime import UTC, datetime
from typing import Any

from ..ledger import MAX_OUTPUT_BYTES, chain, redact
from ..models import EventFlags, EventKind, LedgerEvent, Session
from ..parsers import is_piped

LOG_DIR = os.path.expanduser("~/.receipts/machine")
WIRE_VERSION = 1

BASH_SNIPPET = r"""# >>> receipts recorder (class M) >>>
__receipts_log() { printf '%s\n' "$1" >> "$RECEIPTS_MACHINE_LOG"; }
__receipts_preexec() {
  [ -n "$COMP_LINE" ] && return
  [ "$BASH_COMMAND" = "$PROMPT_COMMAND" ] && return
  __RECEIPTS_CMD=$BASH_COMMAND; __RECEIPTS_T0=$(date +%s.%N)
  __receipts_log "$(RECEIPTS_EVENT=start RECEIPTS_CMD=$__RECEIPTS_CMD receipts _record-line)"
}
__receipts_precmd() {
  local rc=$?
  [ -z "$__RECEIPTS_CMD" ] && return
  __receipts_log "$(RECEIPTS_EVENT=end RECEIPTS_CMD=$__RECEIPTS_CMD RECEIPTS_RC=$rc RECEIPTS_T0=$__RECEIPTS_T0 receipts _record-line)"
  __RECEIPTS_CMD=
}
export RECEIPTS_MACHINE_LOG="${RECEIPTS_MACHINE_LOG:-$HOME/.receipts/machine/$(hostname -s)-$(date +%F).jsonl}"
mkdir -p "$(dirname "$RECEIPTS_MACHINE_LOG")"
trap '__receipts_preexec' DEBUG
PROMPT_COMMAND="__receipts_precmd${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
# <<< receipts recorder (class M) <<<
"""

ZSH_SNIPPET = r"""# >>> receipts recorder (class M) >>>
export RECEIPTS_MACHINE_LOG="${RECEIPTS_MACHINE_LOG:-$HOME/.receipts/machine/$(hostname -s)-$(date +%F).jsonl}"
mkdir -p "${RECEIPTS_MACHINE_LOG:h}"
__receipts_preexec() {
  __RECEIPTS_CMD=$1; __RECEIPTS_T0=$EPOCHREALTIME
  RECEIPTS_EVENT=start RECEIPTS_CMD=$__RECEIPTS_CMD receipts _record-line >> $RECEIPTS_MACHINE_LOG
}
__receipts_precmd() {
  local rc=$?
  [[ -z $__RECEIPTS_CMD ]] && return
  RECEIPTS_EVENT=end RECEIPTS_CMD=$__RECEIPTS_CMD RECEIPTS_RC=$rc RECEIPTS_T0=$__RECEIPTS_T0 \
    receipts _record-line >> $RECEIPTS_MACHINE_LOG
  __RECEIPTS_CMD=
}
autoload -Uz add-zsh-hook
add-zsh-hook preexec __receipts_preexec
add-zsh-hook precmd __receipts_precmd
# <<< receipts recorder (class M) <<<
"""


def install_snippet(shell: str) -> str:
    """The rc-file lines for `shell`. Non-interactive shells need the PATH-first wrapper instead."""
    if shell in ("bash", "sh"):
        return BASH_SNIPPET
    if shell == "zsh":
        return ZSH_SNIPPET
    raise ValueError(f"no recorder snippet for {shell!r}; supported: bash, sh, zsh")


def default_log(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y-%m-%d")
    return os.path.join(LOG_DIR, f"{socket.gethostname().split('.')[0]}-{stamp}.jsonl")


def record_line(env: dict[str, str] | None = None) -> str:
    """Build one wire line from the recorder's environment. Called by `receipts _record-line`."""
    e = dict(os.environ if env is None else env)
    event = e.get("RECEIPTS_EVENT", "end")
    now = float(e.get("RECEIPTS_TS") or datetime.now(tz=UTC).timestamp())
    line: dict[str, Any] = {
        "recorder": "receipts-machine",
        "v": WIRE_VERSION,
        "event": event,
        "ts": now,
        "cmd": e.get("RECEIPTS_CMD", ""),
        "cwd": e.get("PWD", ""),
        "tty": e.get("RECEIPTS_TTY", ""),
        "pid": int(e["RECEIPTS_PID"]) if e.get("RECEIPTS_PID", "").isdigit() else None,
        "ppid": int(e["RECEIPTS_PPID"]) if e.get("RECEIPTS_PPID", "").isdigit() else None,
    }
    if event == "end":
        rc = e.get("RECEIPTS_RC", "")
        line["exit"] = int(rc) if rc.lstrip("-").isdigit() else None
        try:
            line["dur_ms"] = int((now - float(e["RECEIPTS_T0"])) * 1000)
        except (KeyError, ValueError):
            line["dur_ms"] = None
    return json.dumps(redact(line), sort_keys=True)


def _paths(record: dict[str, Any], cwd: str | None) -> list[str]:
    path = record.get("path")
    if not isinstance(path, str) or not path:
        return []
    return [path if os.path.isabs(path) or not cwd else os.path.normpath(os.path.join(cwd, path))]


def parse(path: str) -> tuple[Session, list[LedgerEvent], str | None]:
    """Machine log -> ledger. There is no report here: class M gets it from the UI or the PR."""
    events: list[LedgerEvent] = []
    seq = 0
    started: datetime | None = None
    ended = datetime.fromtimestamp(0, tz=UTC)
    cwd: str | None = None
    host = os.path.basename(path).rsplit("-", 3)[0]
    session_id = f"machine:{os.path.basename(path)}"
    open_calls: dict[str, int] = {}

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("recorder") != "receipts-machine":
                continue
            ts = datetime.fromtimestamp(float(rec.get("ts") or 0), tz=UTC)
            started = started or ts
            ended = max(ended, ts)
            cwd = str(rec.get("cwd") or cwd or "") or None
            kind_name = str(rec.get("event", ""))
            command = str(rec.get("cmd", ""))
            key = f"{rec.get('pid')}:{command}"

            if kind_name == "start":
                events.append(
                    LedgerEvent(
                        seq=seq,
                        ts=ts,
                        session_id=session_id,
                        kind=EventKind.CALL,
                        tool="Bash",
                        cwd=cwd,
                        input=redact(
                            {"command": command, "ppid_chain": rec.get("ppid_chain") or []}
                        ),
                        flags=EventFlags(piped=is_piped(command)),
                    )
                )
                open_calls[key] = seq
                seq += 1
                continue

            if kind_name == "end":
                exit_code = rec.get("exit")
                event = LedgerEvent(
                    seq=seq,
                    ts=ts,
                    session_id=session_id,
                    kind=EventKind.RESULT,
                    tool="Bash",
                    cwd=cwd,
                    input=redact({"command": command}),
                    exit_code=int(exit_code) if isinstance(exit_code, int) else None,
                    duration_ms=rec.get("dur_ms") if isinstance(rec.get("dur_ms"), int) else None,
                    flags=EventFlags(piped=is_piped(command), error=bool(exit_code)),
                )
                out = rec.get("out")
                if isinstance(out, str) and out:
                    blob = str(redact(out))
                    raw = blob.encode()
                    event.output_hash = hashlib.sha256(raw).hexdigest()
                    event.output = raw[:MAX_OUTPUT_BYTES].decode(errors="ignore")
                    event.flags.truncated = len(raw) > MAX_OUTPUT_BYTES
                else:
                    # the shell trap sees status, never stdout: outcome is known, output is not
                    event.flags.stderr_dropped = True
                open_calls.pop(key, None)
                events.append(event)
                seq += 1
                continue

            if kind_name == "fs":
                events.append(
                    LedgerEvent(
                        seq=seq,
                        ts=ts,
                        session_id=session_id,
                        kind=EventKind.RESULT,
                        tool="Edit",
                        cwd=cwd,
                        paths=_paths(rec, cwd),
                        input={"op": str(rec.get("op", "modified")), "watcher": "fs"},
                        exit_code=0,
                    )
                )
                seq += 1
                continue

            if kind_name == "git":
                events.append(
                    LedgerEvent(
                        seq=seq,
                        ts=ts,
                        session_id=session_id,
                        kind=EventKind.RESULT,
                        tool="Git",
                        cwd=cwd,
                        exit_code=0,
                        input=redact(
                            {
                                "ref": str(rec.get("ref", "")),
                                "from": str(rec.get("from", "")),
                                "to": str(rec.get("to", "")),
                                "subject": str(rec.get("subject", "")),
                            }
                        ),
                    )
                )
                seq += 1

    chained = chain(events)
    meta = Session(
        id=session_id,
        source="machine",
        agent=f"unknown ({host})",
        started=started,
        ended=ended,
        cwd=cwd,
        n_events=len(chained),
        ledger_root_hash=chained[-1].hash if chained else "",
        # no tool attribution and no captured stdout: honest about what this class can settle
        integrity_score=0.6,
    )
    return meta, chained, None


def merge_exit_codes(
    ledger: list[LedgerEvent], machine: list[LedgerEvent], *, window_s: float = 5.0
) -> int:
    """Fill missing exit codes on harness RESULTs from machine rows with the same command nearby.

    Timing is a weak join, so this only ever *adds* an exit code the harness never had; it never
    overwrites one, never creates an event, and never changes tool attribution.
    """
    filled = 0
    by_command: dict[str, list[LedgerEvent]] = {}
    for m in machine:
        if m.kind is EventKind.RESULT and m.exit_code is not None and m.input:
            by_command.setdefault(str(m.input.get("command", "")), []).append(m)
    for e in ledger:
        if e.kind is not EventKind.RESULT or e.exit_code is not None or not e.input:
            continue
        candidates = by_command.get(str(e.input.get("command", "")), [])
        near = [m for m in candidates if abs((m.ts - e.ts).total_seconds()) <= window_s]
        if len(near) == 1:
            e.exit_code = near[0].exit_code
            e.flags.error = e.flags.error or bool(near[0].exit_code)
            filled += 1
    return filled
