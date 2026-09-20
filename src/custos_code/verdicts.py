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

from . import parsers, rerun
from .judge import Backend, window_for_all
from .models import Claim, ClaimType, EventKind, LedgerEvent, Verdict, VerdictRecord
from .rules import check as rules_check

# Claim types that only a tool log can witness. Edits, commits and deletions survive in state, so
# they stay settleable on a class-R record; these do not.
NEEDS_TOOL_LOG = frozenset(
    {
        ClaimType.RUN_CMD,
        ClaimType.RUN_TESTS,
        ClaimType.BUILD,
        ClaimType.DEPLOY,
        ClaimType.READ,
        ClaimType.OBSERVED_OUTPUT,
        ClaimType.VERIFY,
    }
)


def known_incomplete(ledger: list[LedgerEvent]) -> str | None:
    """The adapter's own statement that this record has no tool log (see adapters/state.py)."""
    for event in ledger:
        if event.kind is EventKind.META and (event.input or {}).get("event") == "no_tool_log":
            return str((event.input or {}).get("note") or "the record has no tool log")
    return None


def _enforce(rec: VerdictRecord) -> VerdictRecord:
    if rec.method == "judge" and rec.verdict == Verdict.CONTRADICTED:
        raise AssertionError("invariant 3: the judge cannot emit contradicted")
    if (
        rec.verdict == Verdict.CONFIRMED
        and not rec.evidence
        and rec.method not in ("state", "rerun")
    ):
        raise AssertionError("confirmed without evidence or a state check")
    return rec


def _escalates(rec: VerdictRecord | None) -> bool:
    """True when the judge could add something a rule could not."""
    if rec is None:
        return True
    return rec.verdict == Verdict.UNWITNESSED and rec.tier >= 4


def _tier3_candidate(claim: Claim, rec: VerdictRecord, repo_root: str | None) -> bool:
    """Whether an incomplete deterministic test verdict should start/consult a re-run."""
    if repo_root is None:
        return False
    return (
        claim.type == ClaimType.RUN_TESTS
        and rec.verdict in (Verdict.UNRECORDED, Verdict.UNWITNESSED)
        and rerun.can_rerun_tests(repo_root)
    )


def _from_rerun(claim: Claim, event: LedgerEvent) -> VerdictRecord:
    """Turn a finished Tier 3 event into the claim verdict it proves."""
    parsed = parsers.parse(event.output or "", event.exit_code)
    evidence = [event.seq] if event.seq >= 0 else []
    if event.flags.timed_out:
        return VerdictRecord(
            claim_id=claim.id,
            verdict=Verdict.UNRECORDED,
            tier=3,
            method="rerun",
            confidence=0.8,
            evidence=evidence,
            rationale="Tier 3 re-run timed out before producing a test result.",
        )
    if event.flags.truncated and parsed is None:
        return VerdictRecord(
            claim_id=claim.id,
            verdict=Verdict.UNRECORDED,
            tier=3,
            method="rerun",
            confidence=0.7,
            evidence=evidence,
            rationale="Tier 3 re-run output was truncated before a recognisable test summary.",
        )
    if parsed is not None:
        if parsed.collected == 0 and parsed.passed == 0:
            verdict = Verdict.CONTRADICTED
            why = f"Tier 3 re-run collected 0 tests with {parsed.runner}; nothing ran."
        elif parsed.failed or parsed.errors:
            verdict = Verdict.CONTRADICTED
            why = (
                f"Tier 3 re-run with {parsed.runner}: "
                f"{parsed.passed} passed, {parsed.failed} failed, {parsed.errors} errors."
            )
        elif event.exit_code not in (None, 0) or event.flags.error:
            verdict = Verdict.CONTRADICTED
            why = f"Tier 3 re-run with {parsed.runner} exited non-zero."
        else:
            qualifier = None
            for obj in claim.objects:
                n = obj.split("/")[-1] if "/" in obj else obj
                if n.isdigit() and int(n) != parsed.passed:
                    qualifier = f"{obj} claimed, {parsed.passed} passed"
            if qualifier:
                return VerdictRecord(
                    claim_id=claim.id,
                    verdict=Verdict.QUALIFIED,
                    tier=3,
                    method="rerun",
                    confidence=0.85,
                    evidence=evidence,
                    rationale=f"Tier 3 re-run with {parsed.runner}: {parsed.passed} passed, 0 failed.",
                    qualifier=qualifier,
                )
            verdict = Verdict.CONFIRMED
            why = f"Tier 3 re-run with {parsed.runner}: {parsed.passed} passed, 0 failed."
        return VerdictRecord(
            claim_id=claim.id,
            verdict=verdict,
            tier=3,
            method="rerun",
            confidence=0.9,
            evidence=evidence,
            rationale=why,
        )
    if event.exit_code not in (None, 0) or event.flags.error:
        return VerdictRecord(
            claim_id=claim.id,
            verdict=Verdict.CONTRADICTED,
            tier=3,
            method="rerun",
            confidence=0.85,
            evidence=evidence,
            rationale=f"Tier 3 re-run failed (exit {event.exit_code if event.exit_code is not None else 'non-zero'}).",
        )
    if event.exit_code == 0:
        return VerdictRecord(
            claim_id=claim.id,
            verdict=Verdict.CONFIRMED,
            tier=3,
            method="rerun",
            confidence=0.75,
            evidence=evidence,
            rationale="Tier 3 re-run exited 0, but no known runner summary was parsed.",
        )
    return VerdictRecord(
        claim_id=claim.id,
        verdict=Verdict.UNRECORDED,
        tier=3,
        method="rerun",
        confidence=0.6,
        evidence=evidence,
        rationale="Tier 3 re-run could not determine a test outcome.",
    )


def _maybe_tier3(claim: Claim, rec: VerdictRecord, repo_root: str | None, report_seq: int) -> VerdictRecord:
    """Use a completed Tier 3 re-run, or spawn one for a future pass."""
    if not _tier3_candidate(claim, rec, repo_root):
        return rec
    assert repo_root is not None
    status = rerun.poll(claim.session_id, claim.id)
    if status is rerun.RerunStatus.DONE:
        event = rerun.load_result(claim.session_id, claim.id)
        if event is not None:
            return _from_rerun(claim, event)
    if status is rerun.RerunStatus.NONE:
        rerun.spawn_async(claim.session_id, claim.id, repo_root, report_seq)
        return VerdictRecord(
            claim_id=claim.id,
            verdict=Verdict.UNRECORDED,
            tier=3,
            method="rerun",
            confidence=0.7,
            evidence=rec.evidence,
            rationale=f"{rec.rationale} Tier 3 re-run has been started; run the check again after it finishes.",
        )
    return VerdictRecord(
        claim_id=claim.id,
        verdict=Verdict.UNRECORDED,
        tier=3,
        method="rerun",
        confidence=0.7,
        evidence=rec.evidence,
        rationale=f"{rec.rationale} Tier 3 re-run is still running.",
    )


def run(
    claims: list[Claim],
    ledger: list[LedgerEvent],
    repo_root: str | None,
    backend: Backend | None = None,
) -> list[VerdictRecord]:
    settled: dict[str, VerdictRecord] = {}
    pending: list[Claim] = []
    report_seq = max((e.seq for e in ledger), default=-1)
    for claim in claims:
        found = rules_check(claim, ledger, repo_root)
        if _escalates(found):
            pending.append(claim)
        rec = (
            found
            if found is not None
            else VerdictRecord(
                claim_id=claim.id,
                verdict=Verdict.UNWITNESSED,
                tier=4,
                method="rule",
                confidence=0.5,
                evidence=[],
                rationale="No deterministic rule applies to this claim type.",
            )
        )
        rec = _maybe_tier3(claim, rec, repo_root, report_seq)
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

    note = known_incomplete(ledger)
    if note:
        for claim in claims:
            rec = settled[claim.id]
            if rec.verdict is Verdict.UNWITNESSED and claim.type in NEEDS_TOOL_LOG:
                rec.verdict = Verdict.UNRECORDED
                rec.rationale = f"{note}; this claim needs one."
    return [settled[c.id] for c in claims]


def summary(records: list[VerdictRecord]) -> dict[str, int]:
    s = {v.value: 0 for v in Verdict}
    for r in records:
        s[r.verdict.value] += 1
    return s
