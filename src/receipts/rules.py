"""Tiers 1–2: deterministic witness and outcome checks. No model.

Tier 1 (witnessed): does the ledger contain the claimed action? Path matcher for edit/create/read;
command matcher for run_cmd; runner detector for run_tests/build.
Tier 2 (outcome): did it succeed the way the report says? Requires ALL of:
  (a) the invoked binary resolves to a known runner outside the repo tree (defeats ./pytest wrappers),
  (b) runner-format output parsed by parsers.py (defeats echo "47 passed"),
  (c) the runner's own exit code (defeats swallowed subshells).
Pipes and truncation -> `unrecorded`, never `confirmed`.
Edit/create claims additionally need filesystem or git agreement (invariant 5).
`qualified`: "tests pass" when the test set changed since task start (OPEN_QUESTIONS P5).

Owner: Oliver (rules), Anush (runner parsers in parsers.py).
"""
from __future__ import annotations

from .models import Claim, LedgerEvent, VerdictRecord


def check(claim: Claim, ledger: list[LedgerEvent], repo_root: str) -> VerdictRecord | None:
    """Return a verdict if a rule settles the claim, else None (escalate)."""
    raise NotImplementedError
