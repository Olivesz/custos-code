#!/usr/bin/env python3
"""G5 ("Coverage is 7%"): run the same corpus with tiers progressively enabled -- rules only;
+ re-run; + judge -- and report coverage and accusation count at each step. Answers two things
at once: how much coverage each tier buys, and whether a tier is dead weight (moves neither
number) or earns its place (moves coverage without adding accusations).

**Not run against the team's 93-session/121-claim gold corpus.** That corpus lives on whoever
ran the original G5 measurement's machine; this session has none of it (0/10 of the SWE-chat-half
gold ids exist locally either, per issue #27's own mining tool). What *is* available here is this
machine's own local Claude Code transcripts -- this project's own dogfooding sessions -- usable
for the "+ re-run" tier specifically because their `cwd` is this very repo, a real git checkout
that still exists and can be worktree-replayed. Treat every number below as a small local sample,
not a replication of the cited 7%.

The "+ judge" tier is not run: it needs OPENAI_API_KEY/ANTHROPIC_API_KEY, neither of which is set
in this environment. Reported as "skipped", not zero.

The "+ re-run" tier is a real gap this script fills in, not just measures: nothing in
`verdicts.run`/`rules.py` actually calls `rerun.rerun_tests` today (docs/OPEN_QUESTIONS.md E4:
"rule_run_tests has no Tier-3 escalation path, so no claim triggers an async re-run today"). This
script *is* that escalation path, built here only to measure the ablation: for each claim still
`unwitnessed` after rules, if it names a test/build runner, replay the repo's real committed test
command for real, in an isolated worktree (`rerun.rerun_tests`), and reclassify from that output
with the same `parsers.parse` the ladder itself uses -- a claim only moves if a real command,
replayed for real, gives a clean answer.

Usage: uv run python eval/coverage_ablation.py [--timeout 30]
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from receipts import claims as claims_mod  # noqa: E402
from receipts import parsers  # noqa: E402
from receipts import rerun as rerun_mod  # noqa: E402
from receipts.adapters import claude_code  # noqa: E402
from receipts.models import Claim, ClaimType, Verdict, VerdictRecord  # noqa: E402
from receipts.verdicts import run as verdicts_run  # noqa: E402

console = Console()
_RERUNNABLE = frozenset({ClaimType.RUN_TESTS, ClaimType.BUILD})


@dataclass
class Session:
    id: str
    cwd: str
    ledger: list
    report: str


@dataclass
class TierResult:
    name: str
    total: int = 0
    covered: int = 0  # not unwitnessed
    accusations: int = 0  # contradicted
    note: str = ""


def local_sessions() -> list[Session]:
    """Local transcripts with both a report and a `cwd` that is still a real directory on this
    machine -- the re-run tier needs to replay a real repo."""
    out: list[Session] = []
    for path in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
        try:
            sess, ledger, report = claude_code.parse(path)
        except Exception:  # noqa: BLE001 -- a malformed transcript should not kill the whole run
            continue
        if report and report.strip() and sess.cwd and os.path.isdir(sess.cwd):
            out.append(Session(sess.id, sess.cwd, ledger, report))
    return out


def _score(recs: list[VerdictRecord]) -> tuple[int, int]:
    covered = sum(1 for r in recs if r.verdict != Verdict.UNWITNESSED)
    accused = sum(1 for r in recs if r.verdict == Verdict.CONTRADICTED)
    return covered, accused


def tier_rules_only(sessions: list[Session]) -> tuple[TierResult, dict[str, list[tuple[Claim, VerdictRecord]]]]:
    tr = TierResult("rules only")
    by_session: dict[str, list[tuple[Claim, VerdictRecord]]] = {}
    for s in sessions:
        cl = claims_mod.extract_regex(s.report, s.id)
        recs = verdicts_run(cl, s.ledger, s.cwd)
        by_session[s.id] = list(zip(cl, recs, strict=True))
        covered, accused = _score(recs)
        tr.total += len(recs)
        tr.covered += covered
        tr.accusations += accused
    return tr, by_session


def _reclassify_from_rerun(claim: Claim, rec: VerdictRecord, cwd: str, timeout_s: int) -> VerdictRecord:
    """Same decision shape rules._outcome_of uses for a real CALL/RESULT pair, applied to a real
    RERUN event instead: no runner-parseable output leaves the claim exactly as rules found it."""
    try:
        event = rerun_mod.rerun_tests(cwd, timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001 -- a broken local checkout must not crash the ablation
        rec.rationale += f" [rerun failed to start: {exc}]"
        return rec
    if event.flags.timed_out:
        rec.rationale += " [rerun timed out]"
        return rec
    parsed = parsers.parse(event.output or "", event.exit_code)
    if parsed is None:
        rec.rationale += " [rerun output not recognised by any parser]"
        return rec
    if parsed.collected == 0 and parsed.passed == 0:
        return VerdictRecord(claim_id=claim.id, verdict=Verdict.CONTRADICTED, tier=3, method="rerun",
                             confidence=0.9, evidence=[], rationale=f"Re-run collected 0 tests ({parsed.runner}).")
    if parsed.failed or parsed.errors:
        return VerdictRecord(claim_id=claim.id, verdict=Verdict.CONTRADICTED, tier=3, method="rerun",
                             confidence=0.9, evidence=[],
                             rationale=f"Re-run: {parsed.passed} passed, {parsed.failed} failed ({parsed.runner}).")
    if event.exit_code not in (None, 0):
        return VerdictRecord(claim_id=claim.id, verdict=Verdict.CONTRADICTED, tier=3, method="rerun",
                             confidence=0.85, evidence=[], rationale=f"Re-run exited {event.exit_code}.")
    return VerdictRecord(claim_id=claim.id, verdict=Verdict.CONFIRMED, tier=3, method="rerun", confidence=0.9,
                         evidence=[], rationale=f"Re-run: {parsed.passed} passed, 0 failed ({parsed.runner}).")


def tier_plus_rerun(sessions: list[Session], by_session: dict[str, list[tuple[Claim, VerdictRecord]]],
                    timeout_s: int) -> TierResult:
    tr = TierResult("+ re-run")
    cwd_by_session = {s.id: s.cwd for s in sessions}
    for sid, pairs in by_session.items():
        cwd = cwd_by_session[sid]
        for claim, rec in pairs:
            tr.total += 1
            if rec.verdict == Verdict.UNWITNESSED and claim.type in _RERUNNABLE:
                rec = _reclassify_from_rerun(claim, rec, cwd, timeout_s)
            if rec.verdict != Verdict.UNWITNESSED:
                tr.covered += 1
            if rec.verdict == Verdict.CONTRADICTED:
                tr.accusations += 1
    return tr


def render(tiers: list[TierResult], total_claims: int) -> Table:
    t = Table(show_header=True, header_style="dim", title="G5 coverage ablation (local sessions, not the gold corpus)")
    for col in ("tier", "coverage", "accusations", "note"):
        t.add_column(col)
    for tier in tiers:
        if tier.note and tier.covered == 0 and tier.accusations == 0:
            pct, acc = "—", "—"
        else:
            pct = f"{tier.covered}/{total_claims} ({tier.covered / total_claims:.0%})" if total_claims else "—"
            acc = str(tier.accusations)
        t.add_row(tier.name, pct, acc, tier.note)
    return t


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--timeout", type=int, default=30, help="Per-claim rerun timeout, seconds.")
    args = ap.parse_args(argv)

    sessions = local_sessions()
    if not sessions:
        console.print("[red]no local sessions with a report and a still-existing cwd found[/]")
        return 1

    console.print(f"[dim]{len(sessions)} local session(s) -- NOT the team's gold corpus; see module docstring[/]")
    rules_tier, by_session = tier_rules_only(sessions)
    rerun_tier = tier_plus_rerun(sessions, by_session, args.timeout)
    judge_tier = TierResult("+ judge", total=rules_tier.total, note="skipped: no OPENAI_API_KEY/ANTHROPIC_API_KEY")

    console.print(render([rules_tier, rerun_tier, judge_tier], rules_tier.total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
