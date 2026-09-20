"""A model may not convict on its own. AGENTS.md invariant 3.

`verdicts._enforce` raises if the tiered ladder emits a judge-produced `contradicted`. When
`review.py` became the default path it never called that, so a single non-deterministic model call
could block a turn with nothing corroborating it. Every bad block observed traces here: a user lost
four minutes to three auto-mode passes over a `contradicted` on a TRUE statement, and it could not
be reproduced afterwards because it was variance.

Requiring corroboration cost nothing on the fixtures -- 89%, every trap family unchanged, and
run-to-run variance fell to 0.0% -- because real traps always have deterministic backing. Only the
variance-driven accusations were model-only.
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
