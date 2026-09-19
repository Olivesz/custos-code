"""Tier 3: re-execute the claimed check on the final tree.

Runs the repo's committed test/build configuration (not the command the agent typed) in a
git worktree with a timeout, and appends the result to the ledger as a RERUN event so the
re-run is itself auditable. On by default for run_tests/build claims when expected < 60 s;
async in the Stop hook (E3, E4).

Owner: Anush.
"""
from __future__ import annotations

from .models import LedgerEvent


def rerun_tests(repo_root: str, timeout_s: int = 60) -> LedgerEvent:
    raise NotImplementedError
