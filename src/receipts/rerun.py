"""Tier 3: re-execute the claimed check on the final tree.

Runs the repo's committed test/build configuration (not the command the agent typed) in a
git worktree with a timeout, and appends the result to the ledger as a RERUN event so the
re-run is itself auditable. On by default for run_tests/build claims when expected < 60 s.

E3 (decided): the worktree is HEAD overlaid with the live working tree (staged, unstaged, and
untracked files included), so uncommitted edits count as part of "the final tree", and the
command is auto-detected from committed config markers (`_detect_test_command`) rather than
the agent's own typed command or an edited `package.json` script that isn't committed yet.
`cmd` lets a caller override auto-detection when it already knows the exact command (mainly
tests, and a future per-claim-type command such as a distinct build vs. test invocation).

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

import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from .models import EventFlags, EventKind, LedgerEvent

MAX_OUTPUT_BYTES = 4096  # matches config.example.toml [ledger].max_output_bytes; no config loader yet

# E3: the repo's own committed test config decides the command, never the command
# the agent typed (defeats an edited `package.json` script or a swallowed exit code).
_TEST_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("pyproject.toml", ["python", "-m", "pytest"]),
    ("pytest.ini", ["python", "-m", "pytest"]),
    ("setup.cfg", ["python", "-m", "pytest"]),
    ("package.json", ["npm", "test", "--silent"]),
    ("go.mod", ["go", "test", "./..."]),
    ("Cargo.toml", ["cargo", "test"]),
)


def _detect_test_command(worktree: Path) -> list[str] | None:
    for marker, command in _TEST_COMMANDS:
        if (worktree / marker).exists():
            return command
    return None


def _materialize_worktree(repo_root: str, worktree: Path) -> None:
    """Checkout HEAD into a throwaway worktree, then overlay the actual working tree on
    top -- staged, unstaged and untracked files included -- so "the final tree" means
    what is on disk right now, not just the last commit. Never mutates repo_root itself.
    """
    subprocess.run(
        ["git", "-C", repo_root, "worktree", "add", "--detach", str(worktree), "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    shutil.copytree(repo_root, worktree, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))


def rerun_tests(
    repo_root: str,
    session_id: str = "",
    seq: int = -1,
    timeout_s: int = 60,
    cmd: list[str] | None = None,
) -> LedgerEvent:
    """Replay the repo's test command against the final tree, in an isolated worktree, and
    return the result as a RERUN event. `session_id`/`seq` default to placeholders -- a caller
    that doesn't have the real ledger identity yet (or the ledger store itself, E8, once it
    exists) renumbers before appending; `run_worker` below passes the real `session_id` since
    it has it. `cmd` overrides auto-detection (E3) when the caller already knows the exact
    command; otherwise the command is auto-detected from the worktree's own committed config
    markers.
    """
    with tempfile.TemporaryDirectory(prefix="receipts-rerun-") as tmp:
        worktree = Path(tmp) / "worktree"
        _materialize_worktree(repo_root, worktree)
        try:
            command = cmd if cmd is not None else _detect_test_command(worktree)
            started = datetime.now(UTC)
            timed_out = False
            if command is None:
                output = "no known test config found (pyproject.toml, package.json, go.mod, Cargo.toml)"
                exit_code: int | None = None
            else:
                try:
                    proc = subprocess.run(
                        command,
                        cwd=worktree,
                        capture_output=True,
                        text=True,
                        timeout=timeout_s,
                    )
                    output = proc.stdout + proc.stderr
                    exit_code = proc.returncode
                except subprocess.TimeoutExpired as exc:
                    timed_out = True
                    stdout = exc.stdout if isinstance(exc.stdout, str) else ""
                    stderr = exc.stderr if isinstance(exc.stderr, str) else ""
                    output = f"{stdout}{stderr}\n[receipts] timed out after {timeout_s}s"
                    exit_code = None
            duration_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        finally:
            subprocess.run(
                ["git", "-C", repo_root, "worktree", "remove", "--force", str(worktree)],
                capture_output=True,
                text=True,
                check=False,
            )

    output_hash = hashlib.sha256(output.encode()).hexdigest()
    truncated = len(output.encode()) > MAX_OUTPUT_BYTES
    stored_output = output[:MAX_OUTPUT_BYTES] if truncated else output

    return LedgerEvent(
        seq=seq,
        ts=started,
        session_id=session_id,
        kind=EventKind.RERUN,
        tool="rerun_tests",
        input={"command": command, "ref": "HEAD+working-tree"},
        output=stored_output,
        output_hash=output_hash,
        exit_code=exit_code,
        paths=[repo_root],
        cwd=str(worktree),
        duration_ms=duration_ms,
        flags=EventFlags(truncated=truncated, timed_out=timed_out),
    )


# --- E4: detached async spawn, so the Stop hook never waits on the above ---


def _sessions_root() -> Path:
    return Path.home() / ".receipts" / "sessions"


def _rerun_dir(session_id: str) -> Path:
    d = _sessions_root() / session_id / "rerun"
    d.mkdir(parents=True, exist_ok=True)
    return d


def spawn_async(
    session_id: str,
    claim_id: str,
    repo_root: str,
    report_seq: int,
    timeout_s: int = 60,
    cmd: list[str] | None = None,
) -> Path:
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
        pending["repo_root"],
        session_id,
        seq=0,
        timeout_s=pending["timeout_s"],
        cmd=pending.get("cmd"),
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
