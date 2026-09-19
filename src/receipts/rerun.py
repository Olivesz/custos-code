"""Tier 3: re-execute the claimed check on the final tree.

Runs the repo's committed test/build configuration (not the command the agent typed) in a
git worktree with a timeout, and appends the result to the ledger as a RERUN event so the
re-run is itself auditable. On by default for run_tests/build claims when expected < 60 s;
async in the Stop hook (E3, E4).

Owner: Anush.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .models import EventFlags, EventKind, LedgerEvent

MAX_OUTPUT_BYTES = 4000

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


def rerun_tests(repo_root: str, timeout_s: int = 60) -> LedgerEvent:
    """Replay the repo's own test command against the final tree, in an isolated
    worktree, and return the result as a RERUN event (E3). `seq` and `session_id`
    are placeholders -- the caller renumbers before appending this to the ledger.
    """
    with tempfile.TemporaryDirectory(prefix="receipts-rerun-") as tmp:
        worktree = Path(tmp) / "worktree"
        _materialize_worktree(repo_root, worktree)
        try:
            command = _detect_test_command(worktree)
            started = datetime.now(UTC)
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
        seq=-1,
        ts=started,
        session_id="",
        kind=EventKind.RERUN,
        tool="rerun_tests",
        input={"command": command, "ref": "HEAD+working-tree"},
        output=stored_output,
        output_hash=output_hash,
        exit_code=exit_code,
        paths=[repo_root],
        cwd=str(worktree),
        duration_ms=duration_ms,
        flags=EventFlags(truncated=truncated),
    )
