"""Runs the ladder: rules -> (rerun) -> judge, and enforces the invariants at the boundary.

This is the only place that assembles VerdictRecords for a session. It asserts that no
judge-produced record is `contradicted`, that every `confirmed` carries evidence or a state
check, and that unsettled claims come back as `unwitnessed` rather than being dropped.

Escalation rule: a rule's verdict stands unless it is a low-confidence `unwitnessed` that the
rule itself marked as semantic (tier 4). Those, and claims with no rule at all, go to the judge,
which can only turn `unwitnessed` into `confirmed` — never the other way, and never into
`contradicted`. So adding a judge can only ever add evidence, not remove a deterministic finding.

Owner: Oliver.
"""
from __future__ import annotations

from .judge import Backend, window_for_all
from .models import Claim, LedgerEvent, Verdict, VerdictRecord
from .rules import check as rules_check


def _enforce(rec: VerdictRecord) -> VerdictRecord:
    if rec.method == "judge" and rec.verdict == Verdict.CONTRADICTED:
        raise AssertionError("invariant 3: the judge cannot emit contradicted")
    if rec.verdict == Verdict.CONFIRMED and not rec.evidence and rec.method not in ("state", "rerun"):
        raise AssertionError("confirmed without evidence or a state check")
    return rec


def _escalates(rec: VerdictRecord | None) -> bool:
    """True when the judge could add something a rule could not."""
    if rec is None:
        return True
    return rec.verdict == Verdict.UNWITNESSED and rec.tier >= 4


def run(claims: list[Claim], ledger: list[LedgerEvent], repo_root: str | None,
        backend: Backend | None = None) -> list[VerdictRecord]:
    settled: dict[str, VerdictRecord] = {}
    pending: list[Claim] = []
    for claim in claims:
        found = rules_check(claim, ledger, repo_root)
        if _escalates(found):
            pending.append(claim)
        rec = found if found is not None else VerdictRecord(
            claim_id=claim.id, verdict=Verdict.UNWITNESSED, tier=4, method="rule", confidence=0.5,
            evidence=[], rationale="No deterministic rule applies to this claim type.")
        settled[claim.id] = _enforce(rec)

    if backend is not None and pending:
        win = window_for_all(ledger, pending)
        for jrec in backend.judge(pending, win):
            if jrec.verdict == Verdict.CONFIRMED:  # the judge may only upgrade unwitnessed
                settled[jrec.claim_id] = _enforce(jrec)
            else:
                prev = settled[jrec.claim_id]
                prev.rationale = jrec.rationale or prev.rationale
                prev.method = "judge"
                prev.tier = 4
    return [settled[c.id] for c in claims]


def summary(records: list[VerdictRecord]) -> dict[str, int]:
    s = {v.value: 0 for v in Verdict}
    for r in records:
        s[r.verdict.value] += 1
    return s
