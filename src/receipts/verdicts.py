"""Runs the ladder: rules -> (rerun) -> judge, and enforces the invariants at the boundary.

This is the only place that assembles VerdictRecords for a session. It asserts that no
judge-produced record is `contradicted`, that every `confirmed` carries evidence or a state
check, and that unsettled claims come back as `unwitnessed` with a "needs judge" rationale
rather than being dropped.

Owner: Oliver.
"""
from __future__ import annotations

from .models import Claim, LedgerEvent, Verdict, VerdictRecord
from .rules import check as rules_check


def _enforce(rec: VerdictRecord) -> VerdictRecord:
    if rec.method == "judge" and rec.verdict == Verdict.CONTRADICTED:
        raise AssertionError("invariant 3: the judge cannot emit contradicted")
    if rec.verdict == Verdict.CONFIRMED and not rec.evidence and rec.method not in ("state", "rerun"):
        raise AssertionError("confirmed without evidence or a state check")
    return rec


def run(claims: list[Claim], ledger: list[LedgerEvent], repo_root: str | None) -> list[VerdictRecord]:
    out: list[VerdictRecord] = []
    for claim in claims:
        rec = rules_check(claim, ledger, repo_root)
        if rec is None:
            rec = VerdictRecord(
                claim_id=claim.id, verdict=Verdict.UNWITNESSED, tier=4, method="rule", confidence=0.5,
                evidence=[], rationale="No deterministic rule applies; needs the judge (not yet wired).",
            )
        out.append(_enforce(rec))
    return out


def summary(records: list[VerdictRecord]) -> dict[str, int]:
    s = {v.value: 0 for v in Verdict}
    for r in records:
        s[r.verdict.value] += 1
    return s
