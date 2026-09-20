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
from custos_code.models import EventFlags, EventKind, LedgerEvent
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
