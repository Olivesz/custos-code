"""Evidence the recorder kept must reach the judge, and a bare `ok` is not a green test suite.

Both defects were found on 2026-09-20 by replaying 96 real sessions through `review.review()`
rather than through the built fixtures. Neither is visible in `eval/arms/`, because a fixture is a
short transcript written to contain its own proof, and both of these are failures of *long, real*
output -- one truncated before the proof, one matched by a regex loose enough to fire on noise.

The measured cost of each, on that sample:
  - the render window hid the proof for ~5 of 12 false accusations and ~4 of 14 bad `qualified`
  - `parse("ok\n")` vouched for a fabricated suite 40 times across 15 of 96 sessions
"""
from __future__ import annotations

from custos_code import parsers
from custos_code.ledger import MAX_OUTPUT_BYTES
from custos_code.models import EventFlags, EventKind, LedgerEvent, Verdict
from custos_code.review import annotate

NEEDLE = "ModuleNotFoundError: No module named 'src'"


def _result(output: str) -> list[LedgerEvent]:
    return [
        LedgerEvent(seq=0, ts="2026-09-20T00:00:00Z", session_id="s", kind=EventKind.CALL,
                    tool="Bash", input={"command": "pytest -q"}, flags=EventFlags()),
        LedgerEvent(seq=1, ts="2026-09-20T00:00:00Z", session_id="s", kind=EventKind.RESULT,
                    tool="Bash", output=output, flags=EventFlags()),
    ]


def test_the_judge_sees_every_byte_the_ledger_kept() -> None:
    """Session 21756df4: the exception was at offset ~640 and the judge called the claim unproven.

    Pinned at the ledger's own cap rather than a literal, so the two can never drift apart again --
    that drift is the whole defect.
    """
    output = "filler line\n" * 60 + NEEDLE + "\n"
    assert len(output) > 600, "fixture must exceed the old window or it proves nothing"
    assert NEEDLE in annotate(_result(output))


def test_output_is_still_bounded_by_the_ledger_cap() -> None:
    """Uncapped rendering would put a multi-megabyte tool result into a billed prompt."""
    rendered = annotate(_result("x" * (MAX_OUTPUT_BYTES * 4)))
    assert rendered.count("x") == MAX_OUTPUT_BYTES


def test_a_bare_ok_is_not_a_passing_go_suite() -> None:
    """`echo ok` must not be annotated `[parsed go test: 1 passed]`.

    `^ok\\s` matched a bare `ok` because `\\s` matches the newline. This is the `echoed_output`
    trap family, which `eval/arms` scores 8/8 -- while on real sessions the deterministic layer
    was vouching for the fake. `review._corroborate` also accepts a parsed result as grounds for
    an accusation to stand, so the same regex could license one.
    """
    for fake in ("ok\n", "ok\nok\nok\n", "ok \n", "PASS\n"):
        assert parsers.parse(fake, 0) is None, f"{fake!r} was read as a test result"


def test_real_go_output_still_parses() -> None:
    """The tightening must not cost the runner it is named after."""
    plain = parsers.parse("ok  \tgithub.com/x/y\t0.012s\nFAIL\tgithub.com/x/z\t0.02s\n", 0)
    assert plain is not None and (plain.passed, plain.failed) == (1, 1)
    cached = parsers.parse("ok  \tgithub.com/x/y\t(cached)\n", 0)
    assert cached is not None and cached.passed == 1
    verbose = parsers.parse("--- PASS: TestA (0.00s)\n--- FAIL: TestB (0.01s)\nFAIL\tx\t0.1s\n", 0)
    assert verbose is not None and (verbose.passed, verbose.failed) == (1, 1)


def _pair(seq: int, output: str) -> list[LedgerEvent]:
    return [
        LedgerEvent(seq=seq, ts="2026-09-20T00:00:00Z", session_id="s", kind=EventKind.CALL,
                    tool="Bash", input={"command": f"cmd-{seq}"}, flags=EventFlags()),
        LedgerEvent(seq=seq + 1, ts="2026-09-20T00:00:00Z", session_id="s", kind=EventKind.RESULT,
                    tool="Bash", output=output, flags=EventFlags()),
    ]


def test_a_normal_session_is_rendered_in_full() -> None:
    """The budget must be invisible until it is needed, or it silently costs accuracy."""
    text = annotate([e for i in range(20) for e in _pair(2 * i, "x" * 3000)])
    assert "NOTE: this session" not in text
    assert text.count("x" * 3000) == 20


def test_a_session_with_huge_outputs_is_shortened_not_dropped() -> None:
    """Before the budget these raised on the API call and `scan` swallowed it.

    The sessions that failed were the ones with the most recorded activity, so the tool was
    blindest exactly where there was most to check.
    """
    from custos_code.review import MAX_LOG_CHARS

    text = annotate([e for i in range(400) for e in _pair(2 * i, "y" * 4096)])
    assert len(text) <= MAX_LOG_CHARS
    assert "shown only to its first" in text
    assert "not evidence of absence" in text, "the judge must be told the cut exists"
    assert "#798 " in text, "an event was dropped when shortening would have sufficed"


def test_a_session_with_too_many_events_keeps_the_recent_ones_and_says_so() -> None:
    """No window makes 40k events fit. Dropping silently is the one thing we must not do.

    A judge that does not know it is reading a fragment reads a missing call as a call that never
    happened, which turns our own truncation into an accusation against the agent.
    """
    from custos_code.review import MAX_LOG_CHARS

    ledger = [e for i in range(20_000) for e in _pair(2 * i, "z" * 200)]
    text = annotate(ledger)
    assert len(text) <= MAX_LOG_CHARS
    assert "EARLIEST are omitted" in text and "`unwitnessed`, never `contradicted`" in text
    assert f"#{2 * 19_999} " in text, "the most recent events must survive"
    assert "#0 " not in text.split("\n", 1)[1], "the oldest events should be the ones dropped"


def test_a_redirect_to_a_file_is_filtered_output() -> None:
    """`pytest -q > results.txt` sends every byte to a file. Nothing witnessed the outcome.

    Two detectors used to disagree: `parsers.is_piped` knew about `> file`, the Claude Code
    adapter's private `_PIPE_RE` did not -- and the adapter and the live hook were the only two
    users of the weaker one. So the flagship path recorded the call unfiltered, `rules._outcome`
    fell through to its exit-status branch, and "all 7 tests pass" came back CONFIRMED on a run
    whose output nobody had read. Identical evidence got opposite verdicts depending on which
    adapter parsed it.

    No fixture in the suite exercised the redirect branch, so widening either regex changed
    nothing in CI. This is that fixture.
    """
    from custos_code import parsers

    for cmd in ("pytest -q > results.txt", "pytest >> ci.log", "pytest | wc -l",
                "pytest | less", "pytest 2>/dev/null", "pytest &> /dev/null"):
        assert parsers.is_piped(cmd), f"{cmd!r} filters output but was recorded as unfiltered"
    for cmd in ("pytest -q", "pytest tests/", "go test ./..."):
        assert not parsers.is_piped(cmd), f"{cmd!r} is not filtered"


def test_a_runner_claim_over_redirected_output_is_not_confirmed() -> None:
    """The end-to-end consequence, through the real rules path."""
    from custos_code.models import Claim, ClaimType
    from custos_code.rules import check

    ledger = [
        LedgerEvent(seq=0, ts="2026-09-20T00:00:00Z", session_id="s", kind=EventKind.CALL,
                    tool="Bash", input={"command": "pytest -q > results.txt"}, flags=EventFlags()),
        LedgerEvent(seq=1, ts="2026-09-20T00:00:00Z", session_id="s", kind=EventKind.RESULT,
                    tool="Bash", output="", exit_code=0,
                    flags=EventFlags(piped=True)),
    ]
    claim = Claim(id="c1", session_id="s", text="all 7 tests pass", type=ClaimType.RUN_TESTS,
                  objects=["7"])
    rec = check(claim, ledger, ".")
    assert rec is not None and rec.verdict is not Verdict.CONFIRMED, \
        "confirmed a run whose entire output went to a file"
