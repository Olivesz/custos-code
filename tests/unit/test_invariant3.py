"""A model may not convict on its own. AGENTS.md invariant 3.

`verdicts._enforce` raises if the tiered ladder emits a judge-produced `contradicted`. When
`review.py` became the default path it never called that, so a single non-deterministic model call
could block a turn with nothing corroborating it. Every bad block observed traces here: a user lost
four minutes to three auto-mode passes over a `contradicted` on a TRUE statement, and it could not
be reproduced afterwards because it was variance.

Regression coverage includes stale failures, unrelated evidence, attribution, and the public
Stop-hook blocking behavior.
"""
from __future__ import annotations

from custos_code.models import (
    Claim,
    ClaimType,
    EventFlags,
    EventKind,
    LedgerEvent,
    Verdict,
    VerdictRecord,
)
from custos_code.review import _corroborate


def _claim(text: str = "the full suite passes") -> Claim:
    return Claim(id="c1", session_id="s", text=text, type=ClaimType.OTHER)


def _rec(verdict: Verdict = Verdict.CONTRADICTED, ev: list[int] | None = None) -> VerdictRecord:
    return VerdictRecord(claim_id="c1", verdict=verdict, tier=4, method="judge",
                         confidence=0.8, evidence=ev if ev is not None else [1],
                         rationale="the model's reasoning")


def _led(output: str = "", exit_code: int | None = 0) -> list[LedgerEvent]:
    return [
        LedgerEvent(seq=0, ts="2026-09-20T00:00:00Z", session_id="s", kind=EventKind.CALL,
                    tool="Bash", input={"command": "pytest -q"}, flags=EventFlags()),
        LedgerEvent(seq=1, ts="2026-09-20T00:00:00Z", session_id="s", kind=EventKind.RESULT,
                    tool="Bash", output=output, exit_code=exit_code, flags=EventFlags()),
    ]


def test_a_model_only_contradiction_is_downgraded() -> None:
    """Nothing deterministic agrees, so it is reported rather than asserted -- and cannot block."""
    out = _corroborate(_claim(), _rec(), _led(output="everything looks fine", exit_code=0), None)
    assert out.verdict is Verdict.UNRECORDED
    assert "invariant 3" in out.rationale
    assert "the model's reasoning" in out.rationale, "the model's finding must still be surfaced"


def test_a_nonzero_exit_code_corroborates() -> None:
    out = _corroborate(_claim(), _rec(), _led(output="1 failed", exit_code=1), None)
    assert out.verdict is Verdict.CONTRADICTED


PYTEST_FAILING = """============================= test session starts ==============================
collected 5 items

tests/test_a.py ..FF.                                                    [100%]
=========================== 2 failed, 3 passed in 0.12s ========================"""


def test_a_parsed_runner_failure_corroborates() -> None:
    """Real runner output, not an invented string -- the parser requires the session banner, and a
    test built on a fake summary would pass for the wrong reason."""
    out = _corroborate(_claim(), _rec(), _led(output=PYTEST_FAILING, exit_code=0), None)
    assert out.verdict is Verdict.CONTRADICTED


def test_an_empty_collection_corroborates() -> None:
    """`collected 0 items` with exit 0 is the flagship trap: the runner itself says nothing ran."""
    out = _corroborate(_claim(), _rec(), _led(output="collected 0 items\n\nno tests ran in 0.01s", exit_code=0), None)
    assert out.verdict is Verdict.CONTRADICTED


def test_other_verdicts_are_untouched() -> None:
    for v in (Verdict.CONFIRMED, Verdict.QUALIFIED, Verdict.UNWITNESSED, Verdict.UNRECORDED):
        out = _corroborate(_claim(), _rec(v), _led(output="fine"), None)
        assert out.verdict is v, f"{v} was altered"


def test_evidence_that_does_not_exist_cannot_corroborate() -> None:
    """A cited seq that is not in the ledger must not be treated as support."""
    out = _corroborate(_claim(), _rec(ev=[99]), _led(output="fine"), None)
    assert out.verdict is Verdict.UNRECORDED


def test_latest_success_overrules_a_cited_old_failure() -> None:
    from custos_code.review import is_advisory

    led = _led(output=PYTEST_FAILING, exit_code=1)
    success = _led(output="collected 5 items\n5 passed in 0.1s", exit_code=0)
    led += [e.model_copy(update={"seq": e.seq + 2}) for e in success]
    out = _corroborate(_claim(), _rec(), led, None)
    assert is_advisory(out)


def test_unrelated_exit_code_is_not_corroboration() -> None:
    from custos_code.review import is_advisory

    led = _led(output="fatal: unknown revision", exit_code=1)
    led[0].input = {"command": "git show missing"}
    assert is_advisory(_corroborate(_claim(), _rec(), led, None))


def test_sidechain_and_other_sessions_cannot_corroborate() -> None:
    from custos_code.review import is_advisory

    for field in ("sidechain", "session"):
        led = _led(output=PYTEST_FAILING, exit_code=1)
        for e in led:
            if field == "sidechain":
                e.flags.sidechain = True
            else:
                e.session_id = "another-session"
        assert is_advisory(_corroborate(_claim(), _rec(), led, None))


def test_corroboration_returns_the_rule_and_its_actual_evidence() -> None:
    from custos_code.verdicts import _enforce

    out = _corroborate(_claim(), _rec(ev=[99]), _led(output=PYTEST_FAILING, exit_code=1), None)
    assert out.verdict == Verdict.CONTRADICTED
    assert (out.method, out.tier, out.evidence) == ("rule", 2, [0, 1])
    assert _enforce(out) is out


def test_golden_corroboration_cases() -> None:
    import json
    from pathlib import Path

    cases = json.loads((Path(__file__).parents[1] / "golden/corroboration/cases.json").read_text())
    for case in cases:
        led = []
        for cmd, output, code in case["runs"]:
            pair = _led(output=output, exit_code=code)
            pair[0].input = {"command": cmd}
            offset = len(led)
            led.extend(e.model_copy(update={"seq": e.seq + offset}) for e in pair)
        out = _corroborate(_claim(), _rec(), led, None)
        assert out.verdict.value == case["verdict"], case["name"]
