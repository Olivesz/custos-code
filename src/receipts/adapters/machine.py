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

Attribution: class M cannot prove which agent ran a command, so the recorded pid/ppid are a hint
and nothing more. Alone, these rows support outcome claims but leave tool attribution
`unwitnessed` rather than guessing from timing. Merging them into a class-H/F ledger to fill its
exit-code gaps is deliberately not implemented: the join is timing-based, the machine log is
writable by anything the agent runs, and a merge would have to re-chain and mark every borrowed
row to keep invariant 1 legible. See docs/ADAPTERS.md §4.

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
  __RECEIPTS_CMD="$BASH_COMMAND"; __RECEIPTS_T0=$(date +%s.%N)
  __receipts_log "$(RECEIPTS_EVENT=start RECEIPTS_CMD="$__RECEIPTS_CMD" RECEIPTS_PID=$$ RECEIPTS_PPID=$PPID RECEIPTS_TTY="$RECEIPTS_TTY" receipts _record-line)"
}
__receipts_precmd() {
  local rc=$?
  [ -z "$__RECEIPTS_CMD" ] && return
  __receipts_log "$(RECEIPTS_EVENT=end RECEIPTS_CMD="$__RECEIPTS_CMD" RECEIPTS_RC=$rc RECEIPTS_T0="$__RECEIPTS_T0" RECEIPTS_PID=$$ RECEIPTS_PPID=$PPID RECEIPTS_TTY="$RECEIPTS_TTY" receipts _record-line)"
  __RECEIPTS_CMD=
}
export RECEIPTS_TTY="${RECEIPTS_TTY:-$(tty 2>/dev/null || echo)}"
export RECEIPTS_MACHINE_LOG="${RECEIPTS_MACHINE_LOG:-$HOME/.receipts/machine/$(hostname -s)-$(date +%F).jsonl}"
mkdir -p "$(dirname "$RECEIPTS_MACHINE_LOG")"
trap '__receipts_preexec' DEBUG
PROMPT_COMMAND="__receipts_precmd${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
# <<< receipts recorder (class M) <<<
"""

ZSH_SNIPPET = r"""# >>> receipts recorder (class M) >>>
export RECEIPTS_TTY="${RECEIPTS_TTY:-$(tty 2>/dev/null || echo)}"
export RECEIPTS_MACHINE_LOG="${RECEIPTS_MACHINE_LOG:-$HOME/.receipts/machine/$(hostname -s)-$(date +%F).jsonl}"
mkdir -p "${RECEIPTS_MACHINE_LOG:h}"
__receipts_preexec() {
  __RECEIPTS_CMD=$1; __RECEIPTS_T0=$EPOCHREALTIME
  RECEIPTS_EVENT=start RECEIPTS_CMD="$__RECEIPTS_CMD" RECEIPTS_PID=$$ RECEIPTS_PPID=$PPID \
    RECEIPTS_TTY="$RECEIPTS_TTY" receipts _record-line >> "$RECEIPTS_MACHINE_LOG"
}
__receipts_precmd() {
  local rc=$?
  [[ -z $__RECEIPTS_CMD ]] && return
  RECEIPTS_EVENT=end RECEIPTS_CMD="$__RECEIPTS_CMD" RECEIPTS_RC=$rc RECEIPTS_T0="$__RECEIPTS_T0" \
    RECEIPTS_PID=$$ RECEIPTS_PPID=$PPID RECEIPTS_TTY="$RECEIPTS_TTY" \
    receipts _record-line >> "$RECEIPTS_MACHINE_LOG"
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


# docs/ADAPTERS.md §4 promises this ("non-interactive shells: a PATH-first bash and sh wrapper
# that logs argv and exit status and execs the real shell") and §7's VERIFY names exactly the gap
# it closes: "DEBUG trap behaviour inside the shells Claude Code and Codex spawn (they run
# `bash -c`/`zsh -lc`)". They do not source ~/.bashrc -- POSIX shells only read startup files for
# interactive or login shells, and `bash -c "cmd"` is neither -- so BASH_SNIPPET's `trap ... DEBUG`
# and ZSH_SNIPPET's `preexec` hook never attach inside a command an agent spawns this way. Nothing
# in this module implemented the wrapper before now; `install_snippet` only ever returned the
# interactive rc-file forms.
#
# `{real}` is resolved once, at install time, to an absolute path outside the wrapper's own
# directory (the same "bake in the real path instead of re-resolving through PATH" idiom already
# used by `hooks._run.sh`'s `receipts_cmd` and `rerun._worker_argv`) -- re-resolving "bash" via
# PATH inside the wrapper would just find itself again if its own directory is still first.
WRAPPER_TEMPLATE = r"""#!/usr/bin/env bash
# >>> receipts recorder (class M), PATH-first wrapper >>>
# Installed by `receipts record --wrapper`; intercepts a PATH lookup for {name} that an
# agent-spawned, non-interactive shell (`{name} -c "cmd"`) would otherwise resolve straight to the
# real interpreter, invisibly to install_snippet's rc-file hooks. Logs start/end the same way the
# interactive snippets do (`receipts _record-line`, same wire format), then runs the real {name}
# and exits with its exact status. Never captures stdout/stderr: those pass straight through.
REAL="{real}"
if [ "$1" = "-c" ] && [ "$#" -ge 2 ]; then
  __RECEIPTS_CMD="$2"
else
  __RECEIPTS_CMD="$*"
fi
export RECEIPTS_MACHINE_LOG="${{RECEIPTS_MACHINE_LOG:-$HOME/.receipts/machine/$(hostname -s)-$(date +%F).jsonl}}"
mkdir -p "$(dirname "$RECEIPTS_MACHINE_LOG")"
__T0=$(date +%s.%N)
RECEIPTS_EVENT=start RECEIPTS_CMD="$__RECEIPTS_CMD" RECEIPTS_PID=$$ RECEIPTS_PPID=$PPID \
  receipts _record-line >> "$RECEIPTS_MACHINE_LOG" 2>/dev/null || true
"$REAL" "$@"
__RC=$?
RECEIPTS_EVENT=end RECEIPTS_CMD="$__RECEIPTS_CMD" RECEIPTS_RC=$__RC RECEIPTS_T0="$__T0" \
  RECEIPTS_PID=$$ RECEIPTS_PPID=$PPID \
  receipts _record-line >> "$RECEIPTS_MACHINE_LOG" 2>/dev/null || true
exit $__RC
# <<< receipts recorder (class M) <<<
"""

WRAPPER_DIR = os.path.expanduser("~/.receipts/bin")


def wrapper_script(name: str, real_path: str) -> str:
    """Render the PATH-first wrapper for `name` (`bash` or `sh`), calling through to `real_path`."""
    return WRAPPER_TEMPLATE.format(name=name, real=real_path)


def install_wrapper(bin_dir: str | None = None, which: Any = None) -> dict[str, str]:
    """Write `bash`/`sh` wrapper scripts into `bin_dir` (default `~/.receipts/bin`), executable,
    each baked with the real interpreter's current, already-resolved absolute path. Returns
    {name: written_path}; raises FileNotFoundError naming whichever of bash/sh isn't on PATH at
    all, since a wrapper with nothing real to call through to would only break the shell.

    Installing the *directory* onto PATH (ahead of the system one) is the caller's job -- this
    only ever writes files under `bin_dir`, never touches PATH, an rc file, or anything outside it.
    """
    import shutil as _shutil
    import stat

    which = which or _shutil.which
    target = bin_dir or WRAPPER_DIR
    os.makedirs(target, exist_ok=True)
    written: dict[str, str] = {}
    for name in ("bash", "sh"):
        real = which(name)
        if not real or os.path.dirname(os.path.abspath(real)) == os.path.abspath(target):
            raise FileNotFoundError(
                f"no real {name!r} found on PATH outside {target} to wrap -- refusing to install "
                "a wrapper that could only ever call itself"
            )
        path = os.path.join(target, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(wrapper_script(name, os.path.abspath(real)))
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        written[name] = path
    return written


def default_log(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y-%m-%d")
    return os.path.join(LOG_DIR, f"{socket.gethostname().split('.')[0]}-{stamp}.jsonl")


def record_line(env: dict[str, str] | None = None) -> str:
    """Build one wire line from the recorder's environment. Called by `receipts _record-line`."""
    e = dict(os.environ if env is None else env)
    event = e.get("RECEIPTS_EVENT", "end")
    now = float(e.get("RECEIPTS_TS") or datetime.now(tz=UTC).timestamp())
    # the snippets pass the shell's own pid; without them, this process's parent *is* that shell
    pid = _pid(e.get("RECEIPTS_PID", "")) or os.getppid()
    line: dict[str, Any] = {
        "recorder": "receipts-machine",
        "v": WIRE_VERSION,
        "event": event,
        "ts": now,
        "cmd": e.get("RECEIPTS_CMD", ""),
        "cwd": e.get("PWD", ""),
        "tty": e.get("RECEIPTS_TTY", ""),
        "pid": pid,
        "ppid": _pid(e.get("RECEIPTS_PPID", "")),
    }
    if event == "end":
        rc = e.get("RECEIPTS_RC", "")
        line["exit"] = int(rc) if rc.lstrip("-").isdigit() else None
        try:
            line["dur_ms"] = int((now - float(e["RECEIPTS_T0"])) * 1000)
        except (KeyError, ValueError):
            line["dur_ms"] = None
    return json.dumps(redact(line), sort_keys=True)


def _pid(value: str) -> int | None:
    return int(value) if value.isdigit() else None


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
