"""Verdict-level pins for the seven Tier 1/2 rules nothing was holding down (issue #87).

Every existing test that touches COMMIT, DELETE, VERIFY, DEPLOY, RUN_CMD, OBSERVED_OUTPUT or
REVIEW_ALL asserts which *type* `claims.classify` assigns, never which *verdict* the rule then
produces. Each of those seven functions could be replaced with `return None` and all 536 tests
still passed -- and `return None` is not a harmless mutant: `check()` dispatches on
`RULES[claim.type]`, so None escalates the claim to tier 4 `unwitnessed` instead of settling it.
A total collapse of the deterministic layer -- the part that produces every `contradicted` the
product has ever emitted -- was invisible.

These tests assert verdict, tier, method and evidence, the way test_sidechain_laundering.py does
for `rule_run_tests`. Each rule gets its confirming case, its contradicting case where it can
produce one, and its abstain case. Tier is asserted explicitly wherever a collapse would show up
only as a silent 1 -> 4 shift (AGENTS.md invariant 7: report the tier and method on every verdict).
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from custos_code.models import (
    Claim,
    ClaimType,
    EventFlags,
    EventKind,
    LedgerEvent,
    Verdict,
    VerdictRecord,
)
from custos_code.rules import check
from custos_code.verdicts import run


def _call(seq: int, command: str, paths: list[str] | None = None) -> LedgerEvent:
    return LedgerEvent(seq=seq, ts=datetime.now(UTC), session_id="s1", kind=EventKind.CALL,
                       tool="Bash", input={"command": command}, paths=paths or [])


def _read(seq: int, paths: list[str]) -> LedgerEvent:
    return LedgerEvent(seq=seq, ts=datetime.now(UTC), session_id="s1", kind=EventKind.CALL,
                       tool="Read", input={"file_path": paths[0]}, paths=paths)


def _result(seq: int, output: str = "", exit_code: int | None = 0,
            flags: EventFlags | None = None) -> LedgerEvent:
    return LedgerEvent(seq=seq, ts=datetime.now(UTC), session_id="s1", kind=EventKind.RESULT,
                       tool="Bash", output=output, exit_code=exit_code,
                       flags=flags or EventFlags())


def _claim(text: str, ctype: ClaimType, objects: list[str] | None = None) -> Claim:
    return Claim(id="c1", session_id="s1", text=text, type=ctype, objects=objects or [])


def _settled(rec: VerdictRecord | None) -> VerdictRecord:
    """A rule that abstains returns None; every case below is one a rule is supposed to settle."""
    assert rec is not None, "the rule abstained on a claim it is supposed to settle"
    return rec


def _git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _head_sha(repo: Path) -> str:
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


# --------------------------------------------------------------------------------------------
# rule_commit
# --------------------------------------------------------------------------------------------
def test_commit_naming_a_real_sha_confirms_from_repo_state(tmp_path: Path) -> None:
    """The only way a reader can tell "committed as abc1234" is true is that abc1234 resolves.

    Chosen because it is the one commit case that needs no ledger at all -- it is pure state, so
    it pins the state branch independently of whether the harness logged the `git commit`.
    """
    repo = _git_repo(tmp_path)
    sha = _head_sha(repo)
    rec = _settled(check(_claim(f"Committed the fix as {sha[:8]}.", ClaimType.COMMIT,
                                [sha[:8]]), [], repo_root=str(repo)))
    assert rec.verdict is Verdict.CONFIRMED
    assert rec.tier == 1 and rec.method == "state"
    assert sha[:8] in rec.rationale


def test_commit_naming_a_sha_the_repo_does_not_have_is_contradicted(tmp_path: Path) -> None:
    """An agent reporting a commit hash that does not exist is the plainest false report there is.

    This is the case the issue proved by hand, and it is the one that has to keep emitting
    `contradicted`: if it degrades to `unwitnessed` the claim stops blocking and nobody is told.
    """
    repo = _git_repo(tmp_path)
    rec = _settled(check(_claim("Committed as deadbee.", ClaimType.COMMIT, ["deadbee"]),
                         [], repo_root=str(repo)))
    assert rec.verdict is Verdict.CONTRADICTED
    assert rec.tier == 1 and rec.method == "state"


def test_a_rejected_push_is_contradicted_at_tier_2(tmp_path: Path) -> None:
    """"Pushed to origin" after a rejected push is the failure mode reviewers actually hit.

    Chosen because it exercises the ledger branch rather than the state branch, and because a
    non-zero exit is positive evidence of failure -- the only thing invariant 2 lets us accuse on.
    """
    ledger = [_call(0, "git push origin main"),
              _result(1, "! [rejected] main -> main (fetch first)", exit_code=1)]
    rec = _settled(check(_claim("Pushed the branch to origin.", ClaimType.COMMIT), ledger,
                         repo_root=str(tmp_path)))
    assert rec.verdict is Verdict.CONTRADICTED
    assert rec.tier == 2 and rec.method == "rule"
    assert rec.evidence == [0, 1]


def test_commit_with_nothing_to_go_on_stays_unwitnessed_at_tier_1() -> None:
    """Silence about a commit is not an accusation, but it must still be a *deterministic* silence.

    Asserted through `verdicts.run` and on the tier, because a collapsed rule produces the same
    `unwitnessed` word at tier 4 -- the verdict alone cannot distinguish "the rule looked and
    found nothing" from "no rule ran", and invariant 7 says the reader must be able to.
    """
    ledger = [_call(0, "ruff check src"), _result(1, "All checks passed!")]
    (rec,) = run([_claim("Committed the fix.", ClaimType.COMMIT)], ledger, None)
    assert rec.verdict is Verdict.UNWITNESSED
    assert rec.tier == 1 and rec.method == "rule"
    assert rec.evidence == []


# --------------------------------------------------------------------------------------------
# rule_deploy
# --------------------------------------------------------------------------------------------
def test_deploy_confirms_off_a_zero_exit_deploy_command() -> None:
    """A deploy claim is only confirmable from the deploy command's own recorded outcome.

    Chosen over a richer output because a deployer prints nothing a parser recognises; this pins
    the exit-status fallback in `_outcome_of`, which is the branch every deploy actually takes.
    """
    ledger = [_call(0, "fly deploy --app prod"), _result(1, "Deployment complete: v42 live")]
    rec = _settled(check(_claim("Deployed to production.", ClaimType.DEPLOY), ledger, None))
    assert rec.verdict is Verdict.CONFIRMED
    assert rec.tier == 2 and rec.method == "rule"
    assert rec.evidence == [0, 1]


def test_deploy_that_exited_non_zero_is_contradicted() -> None:
    """"Deployed to production" over a failed deploy is the costliest false report in the set.

    Non-zero exit is the positive evidence invariant 2 demands, so this is the deploy case the
    rule is allowed to block on.
    """
    ledger = [_call(0, "fly deploy --app prod"),
              _result(1, "Error: release command failed", exit_code=1)]
    rec = _settled(check(_claim("Deployed to production.", ClaimType.DEPLOY), ledger, None))
    assert rec.verdict is Verdict.CONTRADICTED
    assert rec.tier == 2 and rec.method == "rule"
    assert rec.evidence == [0, 1]


def test_deploy_with_no_deploy_command_stays_unwitnessed_at_tier_1() -> None:
    """The invariant-7 case: a collapsed deploy rule looks identical unless the tier is checked.

    `unwitnessed` at tier 1 means "we looked at the log for a deploy and there was none"; the same
    word at tier 4 means "nobody checked". Only the tier separates them, so assert it.
    """
    ledger = [_call(0, "ls -la"), _result(1, "app\ntests\n")]
    (rec,) = run([_claim("Deployed to production.", ClaimType.DEPLOY)], ledger, None)
    assert rec.verdict is Verdict.UNWITNESSED
    assert rec.tier == 1 and rec.method == "rule"
    assert rec.evidence == []


# --------------------------------------------------------------------------------------------
# rule_run_cmd
# --------------------------------------------------------------------------------------------
def test_run_cmd_confirms_the_named_command_from_its_own_result() -> None:
    """"I ran X" is confirmable only against X, not against some other command in the session.

    Chosen with a command that also appears in `_LINTERS` to prove the run_cmd path -- not the
    build path -- is what settles a RUN_CMD claim.
    """
    ledger = [_call(0, "npm run lint"), _result(1, "All checks passed!")]
    rec = _settled(check(_claim("Ran `npm run lint`.", ClaimType.RUN_CMD, ["npm run lint"]),
                         ledger, None))
    assert rec.verdict is Verdict.CONFIRMED
    assert rec.tier == 2 and rec.method == "rule"
    assert rec.evidence == [0, 1]


def test_run_cmd_that_failed_is_contradicted() -> None:
    """Reporting a command as run-and-fine when it exited non-zero is inaccurate self-reporting.

    Chosen because the exit code is unambiguous: no parser, no text heuristic, nothing to argue
    with, so this is the branch that must never quietly weaken.
    """
    ledger = [_call(0, "npm run lint"),
              _result(1, "error: found 3 errors in 2 files", exit_code=2)]
    rec = _settled(check(_claim("Ran `npm run lint`, clean.", ClaimType.RUN_CMD, ["npm run lint"]),
                         ledger, None))
    assert rec.verdict is Verdict.CONTRADICTED
    assert rec.tier == 2 and rec.method == "rule"
    assert rec.evidence == [0, 1]


def test_run_cmd_never_run_is_unwitnessed_at_tier_1_with_no_evidence() -> None:
    """A command absent from the log is `unwitnessed`, never `contradicted` (invariant 2).

    Chosen with a *different* command present in the ledger, so the test also pins that the rule
    matches on the claimed command rather than confirming off whatever ran last.
    """
    ledger = [_call(0, "npm run build"), _result(1, "built in 2.1s")]
    rec = _settled(check(_claim("Ran `npm run lint`.", ClaimType.RUN_CMD, ["npm run lint"]),
                         ledger, None))
    assert rec.verdict is Verdict.UNWITNESSED
    assert rec.tier == 1 and rec.method == "rule"
    assert rec.evidence == []


def test_run_cmd_piped_output_is_unrecorded_not_confirmed() -> None:
    """A piped result hides the outcome; calling that `confirmed` is how the checker gets fooled.

    `unrecorded` is the verdict that tells the user to fix instrumentation, and it is the only
    honest answer when the summary never reached the record.
    """
    ledger = [_call(0, "npm run lint | tail -5"),
              _result(1, "  4 | const x", exit_code=0, flags=EventFlags(piped=True))]
    rec = _settled(check(_claim("Ran `npm run lint`.", ClaimType.RUN_CMD, ["npm run lint"]),
                         ledger, None))
    assert rec.verdict is Verdict.UNRECORDED
    assert rec.tier == 2 and rec.method == "rule"
    assert rec.evidence == [0, 1]


def test_run_cmd_abstains_when_the_claim_names_no_command() -> None:
    """Documents the one RUN_CMD shape that legitimately escalates, so a future None is deliberate.

    Without this, "returns None" reads as indistinguishable from the collapse the rest of this
    file is guarding against.
    """
    assert check(_claim("Ran it.", ClaimType.RUN_CMD), [], None) is None


# --------------------------------------------------------------------------------------------
# rule_observed_output
# --------------------------------------------------------------------------------------------
def test_observed_output_confirms_when_the_value_is_in_a_recorded_result() -> None:
    """"The endpoint returned 429" is checkable only by finding 429 in output the harness wrote.

    Chosen a three-digit status code because the rule harvests bare numbers out of the claim text
    as well as `objects`, and a status code is the commonest real instance of that.
    """
    ledger = [_call(0, "curl -s -o /dev/null -w '%{http_code}' localhost:8000/api"),
              _result(1, "429")]
    rec = _settled(check(_claim("The endpoint returned 429 under load.",
                                ClaimType.OBSERVED_OUTPUT), ledger, None))
    assert rec.verdict is Verdict.CONFIRMED
    assert rec.tier == 2 and rec.method == "rule"
    assert rec.evidence == [1]


def test_observed_output_absent_from_the_record_is_unwitnessed_not_contradicted() -> None:
    """An unquotable number is a missing quote, not a proven lie -- invariant 2 exactly.

    This rule can never emit `contradicted`: the value could have been seen in output the harness
    truncated. Pinning that keeps a future "strictness" change from inventing accusations.
    """
    ledger = [_call(0, "curl -s localhost:8000/api"), _result(1, "200")]
    rec = _settled(check(_claim("The endpoint returned 429 under load.",
                                ClaimType.OBSERVED_OUTPUT), ledger, None))
    assert rec.verdict is Verdict.UNWITNESSED
    assert rec.tier == 2 and rec.method == "rule"
    assert rec.evidence == []


def test_observed_output_abstains_when_there_is_no_value_to_look_for() -> None:
    """Documents the deliberate abstain, so escalation here stays distinguishable from collapse."""
    assert check(_claim("The output looked right.", ClaimType.OBSERVED_OUTPUT), [], None) is None


# --------------------------------------------------------------------------------------------
# rule_review_all
# --------------------------------------------------------------------------------------------
def test_review_all_confirms_only_when_every_named_file_was_opened() -> None:
    """"Reviewed both handlers" is the claim class where an agent skims one file and reports two.

    Chosen the all-read case first so the partial case below is a real contrast and not just an
    assertion that the rule always hedges.
    """
    ledger = [_read(0, ["app/auth.py"]), _read(1, ["app/rate_limit.py"])]
    rec = _settled(check(_claim("Reviewed app/auth.py and app/rate_limit.py.",
                                ClaimType.REVIEW_ALL, ["app/auth.py", "app/rate_limit.py"]),
                         ledger, None))
    assert rec.verdict is Verdict.CONFIRMED
    assert rec.tier == 1 and rec.method == "rule"
    assert rec.evidence == [0, 1]


def test_review_all_downgrades_to_qualified_when_a_named_file_was_never_opened() -> None:
    """The partial-review catch: literally-true-ish prose over a file that was never opened.

    `qualified` with the count in the qualifier is what the user reads; a bare `confirmed` here
    would hand the agent credit for a file it never looked at.
    """
    ledger = [_read(0, ["app/auth.py"])]
    rec = _settled(check(_claim("Reviewed app/auth.py and app/rate_limit.py.",
                                ClaimType.REVIEW_ALL, ["app/auth.py", "app/rate_limit.py"]),
                         ledger, None))
    assert rec.verdict is Verdict.QUALIFIED
    assert rec.tier == 1 and rec.method == "rule"
    assert rec.qualifier == "1 of 2 opened"
    assert rec.evidence == [0]


def test_review_all_counts_files_when_the_claim_gives_a_number_not_paths() -> None:
    """"Went through all 5 call sites" names no path, so the count is the only checkable part.

    Chosen because this branch is the one the regex extractor hits most often on real reports,
    and it is reachable only when `objects` holds no path at all.
    """
    ledger = [_read(0, ["app/a.py"]), _read(1, ["app/b.py"])]
    rec = _settled(check(_claim("Went through all 5 call sites.", ClaimType.REVIEW_ALL),
                         ledger, None))
    assert rec.verdict is Verdict.QUALIFIED
    assert rec.tier == 1 and rec.method == "rule"
    assert rec.qualifier == "2 of 5 opened"


def test_review_all_with_an_unenumerable_scope_is_unwitnessed_at_tier_1() -> None:
    """"Reviewed the whole module" has no scope a rule can enumerate, and must not be confirmed.

    Chosen as the abstain case because confirming an unbounded claim off any read at all is the
    exact over-reach that would make the deterministic layer untrustworthy.
    """
    ledger = [_read(0, ["app/a.py"])]
    rec = _settled(check(_claim("Reviewed the whole module.", ClaimType.REVIEW_ALL), ledger, None))
    assert rec.verdict is Verdict.UNWITNESSED
    assert rec.tier == 1 and rec.method == "rule"


# --------------------------------------------------------------------------------------------
# rule_verify
# --------------------------------------------------------------------------------------------
def test_verify_by_hand_is_unwitnessed_and_never_an_accusation() -> None:
    """A manual browser check leaves no trace; marking it false would be the product's worst bug.

    Chosen as the primary verify pin because it is the one branch that does not delegate to
    another rule, so it isolates `rule_verify` itself.
    """
    rec = _settled(check(_claim("Checked the dashboard by hand.", ClaimType.VERIFY, ["manual"]),
                         [], None))
    assert rec.verdict is Verdict.UNWITNESSED
    assert rec.tier == 1 and rec.method == "rule"
    assert rec.evidence == []


def test_verify_naming_a_command_is_settled_by_that_command_s_result() -> None:
    """A verification claim that names a command is checkable, and must actually be checked.

    Chosen a health-check curl because it is the commonest "verified it works" in real reports,
    and it proves verify routes to the command path rather than shrugging.
    """
    ledger = [_call(0, "curl -sf http://localhost:8000/health"), _result(1, "ok")]
    rec = _settled(check(_claim("Verified the health endpoint.", ClaimType.VERIFY,
                                ["curl -sf http://localhost:8000/health"]), ledger, None))
    assert rec.verdict is Verdict.CONFIRMED
    assert rec.tier == 2 and rec.method == "rule"
    assert rec.evidence == [0, 1]


def test_verify_naming_a_command_that_failed_is_contradicted() -> None:
    """"Verified it works" over a non-zero exit is the report this project exists to catch."""
    ledger = [_call(0, "curl -sf http://localhost:8000/health"),
              _result(1, "curl: (22) The requested URL returned error: 503", exit_code=22)]
    rec = _settled(check(_claim("Verified the health endpoint.", ClaimType.VERIFY,
                                ["curl -sf http://localhost:8000/health"]), ledger, None))
    assert rec.verdict is Verdict.CONTRADICTED
    assert rec.tier == 2 and rec.method == "rule"
    assert rec.evidence == [0, 1]


def test_verify_naming_no_check_at_all_escalates_at_tier_4_not_nothing() -> None:
    """A semantic verify claim is handed to the judge deliberately, with tier 4 saying so.

    Chosen because it is the one place where the rule's own answer and a collapsed rule's answer
    agree on the verdict -- the record must still carry a rule-authored tier 4, not a default one.
    """
    rec = _settled(check(_claim("Confirmed the behaviour is correct.", ClaimType.VERIFY), [], None))
    assert rec.verdict is Verdict.UNWITNESSED
    assert rec.tier == 4 and rec.method == "rule"
    assert "does not name a check" in rec.rationale


# --------------------------------------------------------------------------------------------
# rule_delete
# --------------------------------------------------------------------------------------------
def test_delete_confirms_when_the_file_is_gone_and_a_removal_was_recorded(tmp_path: Path) -> None:
    """Deletion is the one claim where the evidence is an absence, so both halves must agree.

    Chosen with both halves present (log + state) because that is the two-evidence shape
    invariant 4 requires before anything is allowed to read as settled.
    """
    (tmp_path / "app").mkdir()
    ledger = [_call(0, "rm -f app/legacy.py", ["app/legacy.py"]), _result(1, "")]
    rec = _settled(check(_claim("Deleted app/legacy.py.", ClaimType.DELETE, ["app/legacy.py"]),
                         ledger, repo_root=str(tmp_path)))
    assert rec.verdict is Verdict.CONFIRMED
    assert rec.tier == 1 and rec.method == "state"
    assert rec.evidence == [0]


def test_delete_of_a_file_that_still_exists_is_contradicted(tmp_path: Path) -> None:
    """"Removed the legacy module" while it is still on disk is a directly falsifiable claim.

    Chosen a path with a directory component so `accusable` permits the accusation -- the
    narrower sibling case below is what happens when it does not.
    """
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "legacy.py").write_text("still here\n")
    ledger = [_call(0, "rm -f app/legacy.py", ["app/legacy.py"]), _result(1, "")]
    rec = _settled(check(_claim("Deleted app/legacy.py.", ClaimType.DELETE, ["app/legacy.py"]),
                         ledger, repo_root=str(tmp_path)))
    assert rec.verdict is Verdict.CONTRADICTED
    assert rec.tier == 1 and rec.method == "state"


def test_delete_of_an_unresolvable_bare_filename_is_unwitnessed(tmp_path: Path) -> None:
    """A bare `legacy.py` could be any file anywhere; accusing on it is how false positives happen.

    Chosen because three of the engine's four real `contradicted` verdicts on 93 local sessions
    were exactly this -- a path we could not resolve -- which is why `accusable` exists.
    """
    (tmp_path / "legacy.py").write_text("unrelated file of the same name\n")
    ledger = [_call(0, "rm -f legacy.py", ["legacy.py"]), _result(1, "")]
    rec = _settled(check(_claim("Deleted legacy.py.", ClaimType.DELETE, ["legacy.py"]),
                         ledger, repo_root=str(tmp_path)))
    assert rec.verdict is Verdict.UNWITNESSED
    assert rec.tier == 1 and rec.method == "rule"


def test_delete_abstains_when_the_claim_names_no_path() -> None:
    """Documents the deliberate abstain, so escalation stays distinguishable from collapse."""
    assert check(_claim("Cleaned up the dead code.", ClaimType.DELETE), [], None) is None
