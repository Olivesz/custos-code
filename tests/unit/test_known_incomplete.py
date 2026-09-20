"""A class-R record says it has no tool log; shell claims must then read `unrecorded`.

`unwitnessed` means the record is silent; `unrecorded` means the record is known-incomplete and
the instrumentation is what needs fixing. Path C and a log-less Copilot bundle are exactly the
second case (AGENTS.md, verdict vocabulary).
"""

import os

from receipts import adapters, claims, verdicts
from receipts.models import Claim, ClaimType, Verdict

GOLDEN = os.path.join(os.path.dirname(os.path.dirname(__file__)), "golden")


def _run(fixture: str, text: str, ctype: ClaimType) -> Verdict:
    _, ledger, _ = adapters.parse(os.path.join(GOLDEN, fixture))
    claim = Claim(id="c1", session_id="s", text=text, type=ctype)
    return verdicts.run([claim], ledger, None)[0].verdict


def test_devin_path_c_marks_shell_claims_unrecorded() -> None:
    assert (
        _run("devin/bundle.json", "Ran pytest tests/unit locally, 41 passed.", ClaimType.RUN_TESTS)
        is Verdict.UNRECORDED
    )


def test_claim_types_that_state_can_settle_are_untouched() -> None:
    _, ledger, _ = adapters.parse(os.path.join(GOLDEN, "devin/bundle.json"))
    assert verdicts.known_incomplete(ledger)
    commit = Claim(
        id="c2", session_id="s", text="Committed the retry wrapper.", type=ClaimType.COMMIT
    )
    assert verdicts.run([commit], ledger, None)[0].verdict is not Verdict.UNRECORDED


def test_a_complete_record_keeps_unwitnessed() -> None:
    _, ledger, _ = adapters.parse(os.path.join(GOLDEN, "copilot/bundle.json"))
    assert verdicts.known_incomplete(ledger) is None
    claim = Claim(
        id="c3", session_id="s", text="Checked the dashboard by hand.", type=ClaimType.VERIFY
    )
    assert verdicts.run([claim], ledger, None)[0].verdict is Verdict.UNWITNESSED


def test_the_rationale_names_the_gap_end_to_end() -> None:
    _, ledger, report = adapters.parse(os.path.join(GOLDEN, "devin/bundle.json"))
    assert report is not None
    records = verdicts.run(claims.extract(report, "s"), ledger, None)
    unrecorded = [r for r in records if r.verdict is Verdict.UNRECORDED]
    assert unrecorded and all("no tool calls" in r.rationale for r in unrecorded)
