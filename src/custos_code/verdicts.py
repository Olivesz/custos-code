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

import hashlib
import os
import re
import subprocess

from . import claims as claims_mod
from . import parsers
from . import rerun as rerun_mod
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


def apply_reruns(
    claims: list[Claim], records: list[VerdictRecord], ledger: list[LedgerEvent],
) -> list[VerdictRecord]:
    """Settle open test claims from claim-bound, numbered harness reruns.

    Launching remains in the Stop hook so read-only audits never execute repository code.
    A result cannot stand in for a different claim/session or survive later tool activity.
    Legacy events without a launch boundary remain available to the judge, not this rule.
    """
    settled = []
    for claim, rec in zip(claims, records, strict=True):
        ctype = claim.type
        if ctype == ClaimType.OTHER:
            ctype = claims_mod.classify(claim.text) or ClaimType.OTHER
        if ctype not in _RERUNNABLE or claim.polarity != "did" or rec.verdict not in (
            Verdict.UNWITNESSED, Verdict.UNRECORDED,
        ):
            settled.append(rec)
            continue
        candidates = []
        for event in ledger:
            meta = event.input or {}
            boundary = meta.get("report_seq")
            if (event.kind != EventKind.RERUN or event.tool != "rerun_tests"
                    or event.session_id != claim.session_id or event.flags.sidechain
                    or meta.get("claim_id") != claim.id or meta.get("claim_text") != claim.text
                    or meta.get("claim_kind", "run_tests") != ctype.value
                    or not isinstance(boundary, int)
                    or event.seq <= boundary):
                continue
            if any(e.session_id == claim.session_id and not e.flags.sidechain
                   and e.kind in (EventKind.CALL, EventKind.RESULT) and e.seq > boundary
                   for e in ledger):
                continue
            candidates.append(event)
        if not candidates:
            settled.append(rec)
            continue
        event = max(candidates, key=lambda e: e.seq)
        parsed = parsers.parse(event.output or "", event.exit_code)
        verdict = Verdict.UNRECORDED
        why = "Tier 3 re-run has no complete, recognised test outcome."
        qualifier = None
        flags = event.flags
        if not (flags.timed_out or flags.interrupted or flags.truncated or flags.piped):
            if ctype == ClaimType.BUILD:
                if event.exit_code == 0 and not flags.error:
                    verdict = Verdict.CONFIRMED
                    why = "Tier 3 build completed successfully."
                elif event.exit_code not in (None, 0, 126, 127):
                    verdict = Verdict.CONTRADICTED
                    why = f"Tier 3 build failed (exit {event.exit_code})."
            elif parsed is not None and event.exit_code is not None:
                if parsed.failed or parsed.errors or parsed.collected == 0:
                    verdict = Verdict.CONTRADICTED
                    why = (f"Tier 3 {parsed.runner}: {parsed.passed} passed, "
                           f"{parsed.failed} failed, {parsed.errors} errors"
                           + ("; no tests collected." if parsed.collected == 0 else "."))
                elif event.exit_code == 0 and not flags.error and parsed.passed > 0:
                    verdict = Verdict.CONFIRMED
                    why = f"Tier 3 {parsed.runner}: {parsed.passed} passed, 0 failed."
                    counts = [o for o in claim.objects if o.isdigit()]
                    if counts and any(int(n) != parsed.passed for n in counts):
                        verdict = Verdict.QUALIFIED
                        qualifier = f"{', '.join(counts)} claimed, {parsed.passed} passed"
        settled.append(_enforce(VerdictRecord(
            claim_id=claim.id, verdict=verdict, tier=3, method="rerun",
            confidence=0.9, evidence=[event.seq], rationale=why, qualifier=qualifier,
        )))
    return settled


def run(
    claims: list[Claim],
    ledger: list[LedgerEvent],
    repo_root: str | None,
    backend: Backend | None = None,
) -> list[VerdictRecord]:
    settled: dict[str, VerdictRecord] = {}
    pending: list[Claim] = []
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
    return apply_reruns(claims, [settled[c.id] for c in claims], ledger)


def summary(records: list[VerdictRecord]) -> dict[str, int]:
    s = {v.value: 0 for v in Verdict}
    for r in records:
        s[r.verdict.value] += 1
    return s


# ---------------------------------------------------------------------------------------------
# Tier 3 gating: when re-execution is worth launching at all.
#
# `rerun.py` (Anush's) knows HOW to re-run safely -- committed config only, read from the git
# object store so the agent cannot steer it, in a worktree, with a timeout, async off the Stop
# hook's critical path. Nothing decided WHEN, so nothing ever called it.
#
# The failure mode to design against is not a bad re-run, it is a checker that spends a minute
# re-running things that settle nothing. So the gate is deliberately narrow: every condition below
# must hold, and the common case is that none of them do.
# ---------------------------------------------------------------------------------------------

RERUN_BUDGET_PER_SESSION = 2

# Claim types a re-execution can actually settle. Re-running proves a suite passes; it cannot
# prove a file was edited, a commit was made, or a page was read.
_RERUNNABLE = frozenset({ClaimType.RUN_TESTS, ClaimType.BUILD})


def _tree_key(repo_root: str) -> str | None:
    """A fingerprint of the tree a re-run would execute against.

    Re-running the same commit with the same working tree gives the same answer, so the second
    attempt is pure cost. HEAD alone is not enough -- uncommitted edits are part of what the
    report is about (rerun.py's E3) -- so the porcelain status goes in too.
    """
    try:
        head = subprocess.run(["git", "-C", repo_root, "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=5)
        status = subprocess.run(["git", "-C", repo_root, "status", "--porcelain"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if head.returncode != 0:
        return None
    return hashlib.sha256((head.stdout + status.stdout).encode()).hexdigest()[:16]


def should_rerun(claim: Claim, rec: VerdictRecord, repo_root: str | None,
                 already: set[str] | None = None, budget: int = RERUN_BUDGET_PER_SESSION) -> bool:
    """True when re-executing could change this verdict and has not been tried on this tree.

    Every condition has to hold. In order, and each exists because of a way this wastes time:

    1. The verdict is still open. A `confirmed` or `contradicted` is settled on evidence; re-running
       cannot improve it and a disagreement would be a second opinion, not a second source.
    2. The claim is about running something. Tier 3 answers "does it pass", nothing else.
    3. There is a repo. No git work tree means no worktree to materialise, so `rerun_tests` would
       raise -- which is the bug PR #51's review found in the eval harness.
    4. There is a committed runner config. Auto-detection reads HEAD, so a repo with no test
       configuration has nothing to run and would fail for reasons unrelated to the claim.
    5. Budget, and not already tried on this exact tree. Same commit plus same working tree gives
       the same answer, so repeating it is pure latency.
    """
    if claim.polarity != "did" or rec.verdict not in (Verdict.UNWITNESSED, Verdict.UNRECORDED):
        return False
    # `review.py` -- the path that actually ships -- labels every claim `OTHER`, because its one
    # call extracts and judges but does not classify. Keying the gate on the type alone meant it
    # could only ever fire on the superseded ladder, i.e. never. Fall back to the deterministic
    # text classifier in claims.py, which is what the ladder uses anyway.
    ctype = claim.type
    if ctype in (ClaimType.OTHER, None):
        ctype = claims_mod.classify(claim.text) or ClaimType.OTHER
    if ctype not in _RERUNNABLE:
        return False
    if not repo_root or not os.path.isdir(repo_root):
        return False
    if not os.path.isdir(os.path.join(repo_root, ".git")) and \
            not os.path.isfile(os.path.join(repo_root, ".git")):
        return False
    if rerun_command(claim, repo_root) is None:
        return False
    seen = already if already is not None else set()
    if len(seen) >= budget:
        return False
    key = _tree_key(repo_root)
    return key is not None and f"{claim.id}:{key}" not in seen


def rerun_key(claim: Claim, repo_root: str) -> str | None:
    """The dedupe key `should_rerun` checks, for a caller to record after launching."""
    key = _tree_key(repo_root)
    return f"{claim.id}:{key}" if key else None


def rerun_kind(claim: Claim) -> ClaimType:
    return (claims_mod.classify(claim.text) or ClaimType.OTHER
            if claim.type == ClaimType.OTHER else claim.type)


def rerun_command(claim: Claim, repo_root: str) -> list[str] | None:
    """Choose a committed command only for the check the claim actually describes."""
    ctype = rerun_kind(claim)
    if ctype not in _RERUNNABLE:
        return None
    # BUILD also includes lint/typechecking; a successful build cannot verify those.
    if ctype == ClaimType.BUILD and not re.search(r"\b(?:build|built|compil\w*)\b", claim.text, re.I):
        return None
    return rerun_mod.detect_command(repo_root, ctype.value)
