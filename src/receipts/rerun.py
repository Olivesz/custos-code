"""Tier 3: re-execute the claimed check on the final tree.

Runs the repo's committed test/build configuration (not the command the agent typed) in a
git worktree with a timeout, and appends the result to the ledger as a RERUN event so the
re-run is itself auditable. On by default for run_tests/build claims when expected < 60 s.

E4 (async in the Stop hook): `rerun_tests` itself is a blocking call that can take up to
`timeout_s`, far past the ~10 s the product wants the Stop hook to feel responsive within. The
Stop hook (cli.py `_hook stop`) never calls it directly: it calls `spawn_async`, which returns
immediately after handing the run to a detached subprocess, and blocks only on what Tier 0-2
already settled synchronously. The subprocess's entry point is `receipts _hook rerun-worker`
(cli.py), which calls `run_worker` here. Whatever Stop hook pass (or the extension, or
`receipts check`) runs later picks the result up with `poll`/`load_result` -- there is no path
back into the Stop call that spawned it, since Claude Code hooks are one-shot request/response
and that call has already returned.

Owner: Anush.
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from .models import EventFlags, EventKind, LedgerEvent

MAX_OUTPUT_BYTES = 4096  # matches config.example.toml [ledger].max_output_bytes; no config loader yet


def _sessions_root() -> Path:
    return Path.home() / ".receipts" / "sessions"


def _rerun_dir(session_id: str) -> Path:
    d = _sessions_root() / session_id / "rerun"
    d.mkdir(parents=True, exist_ok=True)
    return d


def rerun_tests(repo_root: str, cmd: str, session_id: str, seq: int, timeout_s: int = 60) -> LedgerEvent:
    """Replay `cmd` against a detached worktree of the final tree; never touches the working copy."""
    worktree_dir = tempfile.mkdtemp(prefix="receipts-rerun-")
    started = datetime.now(UTC)
    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", "--quiet", worktree_dir, "HEAD"],
            cwd=repo_root, check=True, capture_output=True, text=True, timeout=30,
        )
        timed_out = False
        try:
            proc = subprocess.run(
                shlex.split(cmd), cwd=worktree_dir, capture_output=True, text=True, timeout=timeout_s,
            )
            stdout, stderr, exit_code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as e:
            timed_out = True
            stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            exit_code = None
        duration_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        full_output = stdout + (("\n" + stderr) if stderr else "")
        truncated = len(full_output.encode()) > MAX_OUTPUT_BYTES
        output = full_output[:MAX_OUTPUT_BYTES]
        return LedgerEvent(
            seq=seq,
            ts=datetime.now(UTC),
            session_id=session_id,
            kind=EventKind.RERUN,
            tool="rerun",
            # NEEDS-DECISION(oliver): EventFlags has no `timed_out` field; a timeout is currently
            # only recoverable from exit_code is None + duration_ms >= timeout_s * 1000. Add one
            # when models.py (shared seam) is next touched, rather than editing it for this alone.
            input={"command": cmd, "worktree": worktree_dir, "timed_out": timed_out},
            output=output,
            output_hash=sha256(full_output.encode()).hexdigest(),
            exit_code=exit_code,
            cwd=worktree_dir,
            duration_ms=duration_ms,
            flags=EventFlags(truncated=truncated),
        )
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_dir],
            cwd=repo_root, capture_output=True, text=True,
        )
        shutil.rmtree(worktree_dir, ignore_errors=True)


def spawn_async(session_id: str, claim_id: str, repo_root: str, cmd: str, report_seq: int, timeout_s: int = 60) -> Path:
    """Launch Tier 3 detached and return immediately; never blocks the caller (E4).

    Idempotent: a claim already pending or already settled is not re-spawned. The child is its
    own `receipts` invocation (`_hook rerun-worker`) rather than an in-process fork, so it
    survives this process's own exit the same way any other backgrounded shell job would.

    VERIFY(E4): does Claude Code kill the Stop hook's process group when the hook script exits,
    and does `start_new_session=True` survive that? If not, the worker needs to be launched by
    something whose lifecycle Claude Code doesn't own (e.g. a small daemon started by
    `receipts watch`) instead of a child of the hook process. Not resolved here; revisit if the
    result file is ever observed to go missing.
    """
    d = _rerun_dir(session_id)
    pending_path = d / f"{claim_id}.pending.json"
    result_path = d / f"{claim_id}.result.json"
    if result_path.exists() or pending_path.exists():
        return pending_path
    pending_path.write_text(json.dumps({
        "claim_id": claim_id,
        "session_id": session_id,
        "repo_root": repo_root,
        "cmd": cmd,
        "report_seq": report_seq,
        "timeout_s": timeout_s,
        "spawned_at": datetime.now(UTC).isoformat(),
    }))
    log_path = d / f"{claim_id}.log"
    with log_path.open("wb") as log:
        subprocess.Popen(  # noqa: S603 -- argv is fixed; no shell, no untrusted input
            # Same invocation shape as hooks/*.sh; there is no receipts/__main__.py, so this is
            # the console-script entry point (pyproject.toml [project.scripts]), not `-m receipts`.
            ["uv", "run", "--quiet", "receipts", "_hook", "rerun-worker", session_id, claim_id],
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    return pending_path


def run_worker(session_id: str, claim_id: str) -> None:
    """Entry point for the detached subprocess `spawn_async` launches. Not called directly."""
    d = _rerun_dir(session_id)
    pending_path = d / f"{claim_id}.pending.json"
    result_path = d / f"{claim_id}.result.json"
    pending = json.loads(pending_path.read_text())
    # NEEDS-DECISION(oliver): real next-seq should come from the session's ledger store once
    # ledger.LedgerStore (SQLite, E8 hash chain) exists; seq=0 is a placeholder until then.
    event = rerun_tests(
        pending["repo_root"], pending["cmd"], session_id, seq=0, timeout_s=pending["timeout_s"],
    )
    result_path.write_text(event.model_dump_json())
    pending_path.unlink(missing_ok=True)


class RerunStatus(StrEnum):
    NONE = "none"
    PENDING = "pending"
    DONE = "done"


def poll(session_id: str, claim_id: str) -> RerunStatus:
    """Whether a Tier 3 job for this claim has been spawned, is still running, or has a result."""
    d = _rerun_dir(session_id)
    if (d / f"{claim_id}.result.json").exists():
        return RerunStatus.DONE
    if (d / f"{claim_id}.pending.json").exists():
        return RerunStatus.PENDING
    return RerunStatus.NONE


def load_result(session_id: str, claim_id: str) -> LedgerEvent | None:
    """The RERUN LedgerEvent once `poll` reports DONE, else None."""
    p = _rerun_dir(session_id) / f"{claim_id}.result.json"
    if not p.exists():
        return None
    return LedgerEvent.model_validate_json(p.read_text())
