import os
import subprocess
from pathlib import Path

from custos_code.adapters import claude_code
from custos_code.claims import extract_regex
from custos_code.models import ClaimType, Verdict
from custos_code.verdicts import run

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "golden", "claude_code", "session.jsonl")


def _repo(tmp_path: Path) -> Path:
    """A repo that matches the fixture's claims: middleware edited, test file created, both changed vs HEAD."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "auth").mkdir()
    (tmp_path / "auth" / "middleware.py").write_text("def middleware(request, call_next):\n    return call_next(request)\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    (tmp_path / "auth" / "middleware.py").write_text("class RateLimiter: ...\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_rate_limit.py").write_text("def test_ok():\n    assert True\n")
    return tmp_path


def test_fixture_receipt_against_matching_repo(tmp_path: Path) -> None:
    sess, ledger, report = claude_code.parse(FIXTURE)
    assert report
    claims = extract_regex(report, sess.id)
    recs = {r.claim_id: r for r in run(claims, ledger, str(_repo(tmp_path)))}
    by_type = {c.type: recs[c.id] for c in claims}
    assert by_type[ClaimType.EDIT].verdict == Verdict.CONFIRMED          # Edit event + git diff agrees
    assert by_type[ClaimType.CREATE].verdict == Verdict.CONFIRMED        # Write event + file exists
    assert by_type[ClaimType.RUN_TESTS].verdict == Verdict.UNRECORDED    # pytest | tail -5: piped
    assert by_type[ClaimType.BUILD].verdict == Verdict.UNRECORDED        # ruff ... 2>/dev/null | head: piped
    assert by_type[ClaimType.VERIFY].verdict == Verdict.UNWITNESSED      # manual curl: never contradicted
    assert by_type[ClaimType.RUN_TESTS].evidence == [10, 11]


def test_fixture_receipt_without_repo_state(tmp_path: Path) -> None:
    """Invariant 5: edit/create claims are never confirmed from the transcript alone."""
    sess, ledger, report = claude_code.parse(FIXTURE)
    claims = extract_regex(report or "", sess.id)
    recs = {r.claim_id: r for r in run(claims, ledger, str(tmp_path / "nowhere"))}
    by_type = {c.type: recs[c.id] for c in claims}
    assert by_type[ClaimType.EDIT].verdict == Verdict.UNWITNESSED
    assert by_type[ClaimType.CREATE].verdict == Verdict.UNWITNESSED
    assert by_type[ClaimType.EDIT].evidence  # the event is still cited


def test_unpiped_failing_run_is_contradicted(tmp_path: Path) -> None:
    from datetime import datetime

    from custos_code.models import Claim, EventFlags, EventKind, LedgerEvent

    ts = datetime(2026, 9, 19)
    ledger = [
        LedgerEvent(seq=0, ts=ts, session_id="s", kind=EventKind.CALL, tool="Bash", input={"command": "pytest -q"}),
        LedgerEvent(seq=1, ts=ts, session_id="s", kind=EventKind.RESULT, tool="Bash",
                    output="============ test session starts ============\ncollected 12 items\n\n1 failed, 11 passed in 0.4s\n",
                    flags=EventFlags(error=True)),
    ]
    claim = Claim(id="c1", session_id="s", text="ran the suite, all 12 passing", type=ClaimType.RUN_TESTS, objects=["12"])
    (rec,) = run([claim], ledger, None)
    assert rec.verdict == Verdict.CONTRADICTED and rec.evidence == [0, 1] and "1 failed" in rec.rationale


def test_count_mismatch_is_contradicted() -> None:
    """A tally the runner disagrees with is a false statement, not a hedge.

    Tier 2 owns this: it is arithmetic over parsed runner output, so invariant 3 permits it. It is
    also the single most demonstrable catch the product has -- the log settles it outright.
    """
    from datetime import datetime

    from custos_code.models import Claim, EventKind, LedgerEvent

    ts = datetime(2026, 9, 19)
    ledger = [
        LedgerEvent(seq=0, ts=ts, session_id="s", kind=EventKind.CALL, tool="Bash", input={"command": "uv run pytest"}),
        LedgerEvent(seq=1, ts=ts, session_id="s", kind=EventKind.RESULT, tool="Bash",
                    output="============ test session starts ============\ncollected 9 items\n\n9 passed in 0.2s\n"),
    ]
    claim = Claim(id="c1", session_id="s", text="all 12 tests pass", type=ClaimType.RUN_TESTS, objects=["12"])
    (rec,) = run([claim], ledger, None)
    assert rec.verdict == Verdict.CONTRADICTED and rec.qualifier == "12 claimed, 9 passed"
    assert "9" in rec.rationale and "12" in rec.rationale


def test_no_judge_contradiction_and_confirmed_needs_evidence() -> None:
    import pytest

    from custos_code.models import VerdictRecord
    from custos_code.verdicts import _enforce

    with pytest.raises(AssertionError):
        _enforce(VerdictRecord(claim_id="c", verdict=Verdict.CONTRADICTED, tier=4, method="judge", confidence=1.0))
    with pytest.raises(AssertionError):
        _enforce(VerdictRecord(claim_id="c", verdict=Verdict.CONFIRMED, tier=1, method="rule", confidence=1.0))


def test_the_test_count_pattern_only_matches_a_tally() -> None:
    """The guard on the one rule that can accuse from a number.

    A count mismatch is the product's most demonstrable catch, which makes it the most dangerous
    place to be wrong: it emits `contradicted` deterministically, with no model in the loop to
    hesitate. Every string on the right was considered and rejected -- `added 12 tests` in
    particular is a claim about writing tests and is consistent with any run total at all.
    """
    from custos_code.rules import _TEST_COUNT_RE

    def found(text: str) -> list[int]:
        return [int(g) for m in _TEST_COUNT_RE.finditer(text) for g in m.groups() if g]

    for text, want in [("all 12 tests pass", [12]), ("81 tests green", [81]), ("85 passed", [85]),
                       ("**81 tests green.**", [81]), ("the suite is 81 passing", [81])]:
        assert found(text) == want, text
    for text in ("ran 3 test files", "pytest 9.1.1", "python 3.14", "wrote 12 tests",
                 "added 12 tests in tests/test_rate_limit.py", "updated 3 test suites",
                 "across 15 of 96 sessions", "2 test modules", "took 0.42s"):
        assert found(text) == [], f"would accuse on {text!r}"


def test_path_matches_pins_what_six_rules_depend_on() -> None:
    """`path_matches` is the primitive six of the seven Tier 1/2 rules resolve claims through.

    Its guard used to read `obj.startswith(("/", ".")) is None`, which is dead code: `startswith`
    returns a bool and is never None, so the clause was always False and the condition was only
    `not obj`. Nothing tested it either way, so neither the bug nor a repair would have shown up.

    The `./` cases are the part that changed. `./src/x.py` and `src/x.py` are the same file and
    agents write both; a leading `./` cannot change which file is meant, so normalising it is not
    a judgement call. Everything else here pins the behaviour the rules were measured with.
    """
    from custos_code.rules import path_matches

    for ledger_path, obj in [("src/cart.py", "src/cart.py"),
                             ("src/cart.py", "cart.py"),          # bare basename
                             ("src/cart.py", "./src/cart.py"),    # was False before
                             ("./src/cart.py", "src/cart.py"),
                             ("src/cart.py", "src/cart.py/")]:    # trailing slash
        assert path_matches(ledger_path, obj), (ledger_path, obj)

    for ledger_path, obj in [("src/cart.py", ""),
                             ("src/cart.py", "./"),
                             ("src/cart.py", "other.py"),
                             ("src/cart.py", "/abs/src/cart.py"),
                             ("src/cart.py", "cart")]:
        assert not path_matches(ledger_path, obj), (ledger_path, obj)
