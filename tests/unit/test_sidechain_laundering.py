"""E10 fixture: the subagent-laundering case (docs/MECHANICS.md §1, docs/OPEN_QUESTIONS.md).

A parent claim like "the tests pass" must not turn `confirmed` off a subagent's own tool call
either -- only off a *main-chain* event. Both independently-implemented paths already enforce
this (`rules._visible` and `review.annotate` each drop `flags.sidechain` events before any
evidence matching happens), but neither had a test pinning the behavior down. This is that test.
"""
from datetime import UTC, datetime

from custos_code.models import Claim, ClaimType, EventFlags, EventKind, LedgerEvent, Verdict
from custos_code.review import annotate
from custos_code.rules import check


def _event(seq: int, kind: EventKind, sidechain: bool, **kw: object) -> LedgerEvent:
    return LedgerEvent(seq=seq, ts=datetime.now(UTC), session_id="s1", kind=kind,
                       flags=EventFlags(sidechain=sidechain), **kw)  # type: ignore[arg-type]


def _subagent_pytest_ledger() -> list[LedgerEvent]:
    """A subagent ran pytest and it passed -- but nothing on the main chain did."""
    return [
        _event(0, EventKind.CALL, sidechain=True, tool="Bash", input={"command": "pytest -q"}),
        _event(1, EventKind.RESULT, sidechain=True, tool="Bash", output="12 passed in 0.4s", exit_code=0),
    ]


def test_rules_never_confirm_a_claim_from_subagent_only_evidence() -> None:
    claim = Claim(id="c1", session_id="s1", text="the tests pass", type=ClaimType.RUN_TESTS)
    rec = check(claim, _subagent_pytest_ledger(), repo_root=None)
    assert rec is not None
    assert rec.verdict == Verdict.UNWITNESSED
    assert rec.evidence == []  # the subagent's own passing result is never cited as the parent's evidence


def test_review_annotate_drops_sidechain_events_entirely() -> None:
    rendered = annotate(_subagent_pytest_ledger())
    assert "pytest" not in rendered
    assert "12 passed" not in rendered
    assert rendered.strip() == ""


def test_rules_still_confirm_from_main_chain_evidence() -> None:
    """Control: the same claim, the same passing output, but on the main chain -- confirms.
    Distinguishes "subagent evidence is untrusted" from "this rule never confirms run_tests"."""
    claim = Claim(id="c1", session_id="s1", text="the tests pass", type=ClaimType.RUN_TESTS)
    ledger = [
        _event(0, EventKind.CALL, sidechain=False, tool="Bash", input={"command": "pytest -q"}),
        _event(1, EventKind.RESULT, sidechain=False, tool="Bash", output="12 passed in 0.4s", exit_code=0),
    ]
    rec = check(claim, ledger, repo_root=None)
    assert rec is not None
    assert rec.verdict == Verdict.CONFIRMED
    assert rec.evidence == [0, 1]
