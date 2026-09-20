"""The checker must not cite evidence the agent has already superseded.

Session 21756df4, 2026-09-19. Auto mode blocked the same claims across three passes, citing the
piped run from pass 1 while the agent had already re-run each check unpiped. The agent said so in
its own report and was right:

    "the stop hook flagged the same claims across all three passes, citing call indices from
     before I re-ran each check as a standalone unpiped command."

`feedback.cleared()` gated the verdict on new evidence. Nothing gated the citation. This is the one
real non-stationarity risk in the auto-mode design (docs/SCOPE.md §2), so it gets its own tests.
"""
from __future__ import annotations

from custos_code.models import EventFlags, EventKind, LedgerEvent
from custos_code.review import SYSTEM, annotate

BOUNDARY = "everything below is NEW"


def _ledger(n: int) -> list[LedgerEvent]:
    out: list[LedgerEvent] = []
    for i in range(n):
        out.append(LedgerEvent(seq=2 * i, ts="2026-09-19T00:00:00Z", session_id="s",
                               kind=EventKind.CALL, tool="Bash",
                               input={"command": f"cmd-{i}"}, flags=EventFlags()))
        out.append(LedgerEvent(seq=2 * i + 1, ts="2026-09-19T00:00:00Z", session_id="s",
                               kind=EventKind.RESULT, tool="Bash", output=f"out-{i}",
                               flags=EventFlags()))
    return out


def test_no_boundary_on_a_first_pass() -> None:
    """Nothing has been superseded yet; the line would be noise and would cost tokens."""
    assert BOUNDARY not in annotate(_ledger(3))
    assert BOUNDARY not in annotate(_ledger(3), nudge_seq=-1)


def test_the_boundary_lands_immediately_after_the_nudge() -> None:
    text = annotate(_ledger(4), nudge_seq=3)
    lines = text.splitlines()
    idx = next(i for i, ln in enumerate(lines) if BOUNDARY in ln)
    assert lines[idx - 1].startswith("#3 "), "boundary drawn in the wrong place"
    assert lines[idx + 1].startswith("#4 "), "boundary drawn in the wrong place"


def test_the_boundary_is_drawn_once() -> None:
    assert annotate(_ledger(5), nudge_seq=2).count(BOUNDARY) == 1


def test_every_event_survives_the_boundary() -> None:
    """Drawing the line must not drop or reorder evidence."""
    led = _ledger(4)
    plain, marked = annotate(led), annotate(led, nudge_seq=3)
    for e in led:
        assert f"#{e.seq} " in marked, f"event #{e.seq} vanished"
    assert len(marked.splitlines()) == len(plain.splitlines()) + 1


def test_a_nudge_past_the_end_draws_nothing() -> None:
    """Blocked, then the agent did no further tool calls: there is no new evidence to point at."""
    assert BOUNDARY not in annotate(_ledger(2), nudge_seq=99)


def test_the_prompt_explains_what_the_line_means() -> None:
    """A marker the model has not been told how to read is decoration."""
    assert "Superseded evidence" in SYSTEM
    assert "boundary" in SYSTEM
    assert "superseded, not repeated" in SYSTEM


def test_the_stale_citation_case_end_to_end() -> None:
    """The shape from 21756df4: a piped run, a nudge, then a clean unpiped re-run."""
    led = [
        LedgerEvent(seq=0, ts="2026-09-19T00:00:00Z", session_id="s", kind=EventKind.CALL,
                    tool="Bash", input={"command": "pytest -q | tail -3"}, flags=EventFlags()),
        LedgerEvent(seq=1, ts="2026-09-19T00:00:00Z", session_id="s", kind=EventKind.RESULT,
                    tool="Bash", output="no tests ran", flags=EventFlags(piped=True)),
        LedgerEvent(seq=2, ts="2026-09-19T00:00:00Z", session_id="s", kind=EventKind.CALL,
                    tool="Bash", input={"command": "pytest -q"}, flags=EventFlags()),
        LedgerEvent(seq=3, ts="2026-09-19T00:00:00Z", session_id="s", kind=EventKind.RESULT,
                    tool="Bash", output="2 passed in 0.05s", exit_code=0, flags=EventFlags()),
    ]
    text = annotate(led, nudge_seq=1)
    before, after = text.split(BOUNDARY)
    assert "tail -3" in before, "the superseded piped run should be above the line"
    assert "2 passed" in after, "the clean re-run should be below the line"
