"""Runs the ladder: rules -> rerun -> judge, and enforces the invariants at the boundary.

This is the only place that assembles VerdictRecords for a session. It asserts that no
judge-produced record is `contradicted`, that every `confirmed` has evidence, and that
`unrecorded` is set when the relevant events carry truncated/piped flags.

Owner: Oliver.
"""
from __future__ import annotations

from .models import Claim, LedgerEvent, VerdictRecord


def run(claims: list[Claim], ledger: list[LedgerEvent], repo_root: str) -> list[VerdictRecord]:
    raise NotImplementedError
