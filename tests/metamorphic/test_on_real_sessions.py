"""Apply the metamorphic relations to every real session on this machine.

A handful of relations times every real confirmed verdict gives hundreds of cases from data we
already have, with no labelling (docs/METHODS.md §1). Skipped in CI, where no transcripts exist.
"""
from __future__ import annotations

import copy
import glob
import os

import pytest

from receipts.adapters import claude_code
from receipts.claims import extract_regex
from receipts.models import EventFlags, EventKind, Verdict
from receipts.verdicts import run

SESSIONS = sorted(glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")), key=os.path.getmtime)[-120:]
pytestmark = pytest.mark.skipif(not SESSIONS, reason="no local transcripts (CI)")


def _confirmed_cases() -> list[tuple[str, object, list[object]]]:
    out = []
    for f in SESSIONS:
        try:
            sess, ledger, report = claude_code.parse(f)
        except Exception:
            continue
        if not report:
            continue
        claims = extract_regex(report, sess.id)
        if not claims:
            continue
        for c, r in zip(claims, run(claims, ledger, sess.cwd), strict=True):
            if r.verdict == Verdict.CONFIRMED:
                out.append((sess.cwd, c, ledger))
    return out


CASES = _confirmed_cases()


def test_there_are_real_confirmed_cases_to_transform() -> None:
    assert CASES, "no confirmed verdicts found; the relations below would be vacuous"


def _same_class(ledger: list, claim: object) -> set[int]:
    """Every event that could witness this claim, not only the ones the verdict happened to cite.

    Stated the narrow way first ("remove the cited events"), MR1 failed on four real commit claims:
    the rule simply found an earlier `git push` and confirmed on that. That is the relation being
    wrong, not the rule — cited evidence is not the only evidence. It did reveal a real looseness in
    `rule_commit`, fixed separately. The honest relation removes the whole class.
    """
    tool_of = {e.seq: e.tool for e in ledger}
    cited_tools = {tool_of.get(s) for s in run([claim], ledger, None)[0].evidence}
    return {e.seq for e in ledger if e.tool in cited_tools}


def test_mr1_removing_all_evidence_of_that_class_never_leaves_it_confirmed() -> None:
    """For every real confirmation, deleting every event that could witness it must drop it."""
    checked = violations = 0
    for cwd, claim, ledger in CASES:
        rec = run([claim], ledger, cwd)[0]
        if not rec.evidence or rec.method == "state":
            continue  # state checks read the filesystem, so the ledger is not their evidence
        drop = _same_class(ledger, claim)
        stripped = [e for e in ledger if e.seq not in drop]
        checked += 1
        if run([claim], stripped, cwd)[0].verdict == Verdict.CONFIRMED:
            violations += 1
    assert checked > 0
    assert violations == 0, f"{violations}/{checked} confirmations survived removal of all evidence of their class"


def test_mr4_appending_an_unrelated_event_never_changes_a_verdict() -> None:
    from datetime import datetime

    from receipts.models import LedgerEvent

    changed = 0
    for cwd, claim, ledger in CASES[:40]:
        before = run([claim], ledger, cwd)[0].verdict
        noisy = copy.deepcopy(ledger)
        noisy.append(LedgerEvent(seq=max((e.seq for e in ledger), default=0) + 1,
                                 ts=datetime(2026, 1, 1), session_id="s", kind=EventKind.CALL,
                                 tool="Read", input={"file_path": "/tmp/unrelated-xyz.md"},
                                 paths=["/tmp/unrelated-xyz.md"]))
        if run([claim], noisy, cwd)[0].verdict != before:
            changed += 1
    assert changed == 0, f"{changed} verdicts moved when an unrelated event was appended"


def test_mr6_moving_evidence_into_a_sidechain_never_leaves_it_confirmed() -> None:
    checked = violations = 0
    for cwd, claim, ledger in CASES:
        rec = run([claim], ledger, cwd)[0]
        if not rec.evidence or rec.method == "state":
            continue  # state checks read the filesystem, not the ledger
        drop = _same_class(ledger, claim)
        laundered = copy.deepcopy(ledger)
        for e in laundered:
            if e.seq in drop:
                e.flags = EventFlags(**{**e.flags.model_dump(), "sidechain": True})
        checked += 1
        if run([claim], laundered, cwd)[0].verdict == Verdict.CONFIRMED:
            violations += 1
    assert violations == 0, f"{violations}/{checked} confirmations survived laundering through a sidechain"


def test_mr7_marking_cited_output_truncated_never_leaves_a_runner_claim_confirmed() -> None:
    from receipts.models import ClaimType

    checked = violations = 0
    for cwd, claim, ledger in CASES:
        if claim.type not in (ClaimType.RUN_TESTS, ClaimType.BUILD, ClaimType.RUN_CMD):
            continue
        rec = run([claim], ledger, cwd)[0]
        if not rec.evidence:
            continue
        cut = copy.deepcopy(ledger)
        for e in cut:
            if e.seq in set(rec.evidence) and e.kind == EventKind.RESULT:
                e.flags = EventFlags(**{**e.flags.model_dump(), "truncated": True})
        checked += 1
        if run([claim], cut, cwd)[0].verdict == Verdict.CONFIRMED:
            violations += 1
    assert violations == 0, f"{violations}/{checked} runner claims stayed confirmed on truncated output"
