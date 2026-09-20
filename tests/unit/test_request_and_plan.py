"""`adapters.request_and_plan`. docs/SCOPE.md §6.3, issue #58.

Scope needs the ask and the agent's self-authored plan, per agent, without scope.py ever learning
which harness produced the ledger. Claude Code and Codex both already write a USER event for the
request, so the extraction is agent-agnostic in practice, not just in principle -- one function,
no per-source branch.
"""
from __future__ import annotations

from datetime import datetime

from custos_code.adapters import request_and_plan
from custos_code.models import EventFlags, EventKind, LedgerEvent, Session

TS = datetime(2026, 9, 20)


def _sess(source: str = "claude_code") -> Session:
    return Session(id="s", source=source, agent=source)


def _user(seq: int, text: str, sidechain: bool = False) -> LedgerEvent:
    return LedgerEvent(seq=seq, ts=TS, session_id="s", kind=EventKind.USER, output=text,
                       flags=EventFlags(sidechain=sidechain))


def _text(seq: int, text: str, sidechain: bool = False) -> LedgerEvent:
    return LedgerEvent(seq=seq, ts=TS, session_id="s", kind=EventKind.TEXT, output=text,
                       flags=EventFlags(sidechain=sidechain))


def _call(seq: int, tool: str, inp: dict | None = None, sidechain: bool = False) -> LedgerEvent:
    return LedgerEvent(seq=seq, ts=TS, session_id="s", kind=EventKind.CALL, tool=tool,
                       input=inp or {}, flags=EventFlags(sidechain=sidechain))


def test_the_request_is_the_first_user_event() -> None:
    ledger = [
        _user(0, "fix the failing tests in tests/ and confirm the suite passes"),
        _call(1, "Read", {"file_path": "tests/test_x.py"}),
        _user(2, "also check the linter"),
    ]
    request, _ = request_and_plan(_sess(), ledger)
    assert request == "fix the failing tests in tests/ and confirm the suite passes"


def test_a_sidechain_user_event_does_not_count_as_the_request() -> None:
    ledger = [_user(0, "sub-agent instructions", sidechain=True), _user(1, "the real ask")]
    request, _ = request_and_plan(_sess(), ledger)
    assert request == "the real ask"


def test_todo_write_is_the_strongest_plan_signal() -> None:
    ledger = [
        _user(0, "add rate limiting"),
        _text(1, "I'll read the middleware first."),
        _call(2, "Read", {"file_path": "middleware.py"}),
        _call(3, "TodoWrite", {"todos": [
            {"content": "add a RateLimiter class", "status": "pending"},
            {"content": "wire it into the middleware", "status": "pending"},
        ]}),
        _call(4, "TodoWrite", {"todos": [
            {"content": "add a RateLimiter class", "status": "completed"},
            {"content": "wire it into the middleware", "status": "in_progress"},
            {"content": "add tests", "status": "pending"},
        ]}),
    ]
    request, plan = request_and_plan(_sess(), ledger)
    assert request == "add rate limiting"
    assert plan == ["add a RateLimiter class", "wire it into the middleware", "add tests"]


def test_falls_back_to_the_last_assistant_message_before_the_first_tool_call() -> None:
    ledger = [
        _user(0, "fix the bug"),
        _text(1, "I'll look at the traceback first."),
        _text(2, "Looks like an off-by-one in parse(); I'll patch it and add a regression test."),
        _call(3, "Read", {"file_path": "parse.py"}),
        _call(4, "Edit", {"file_path": "parse.py"}),
    ]
    _, plan = request_and_plan(_sess(), ledger)
    assert plan == ["Looks like an off-by-one in parse(); I'll patch it and add a regression test."]


def test_no_todo_write_and_no_text_before_the_first_call_is_an_empty_plan() -> None:
    ledger = [_user(0, "just run the tests"), _call(1, "Bash", {"command": "pytest -q"})]
    _, plan = request_and_plan(_sess(), ledger)
    assert plan == []


def test_class_r_falls_back_to_the_report_as_the_spec() -> None:
    """Copilot/Devin without a chat transcript: the PR body is the only text artifact there is,
    and it serves as both the report and, absent anything else, the spec (SCOPE.md §6.3, S4)."""
    ledger = [_call(0, "Bash", {"command": "pytest -q"})]
    request, plan = request_and_plan(_sess("copilot"), ledger, report="## Pull request\nFix flaky test")
    assert request == "## Pull request\nFix flaky test"
    assert plan == []


def test_a_real_user_event_wins_over_the_report_fallback() -> None:
    ledger = [_user(0, "the actual live ask")]
    request, _ = request_and_plan(_sess("devin"), ledger, report="PR body text")
    assert request == "the actual live ask"


def test_nothing_at_all_is_an_empty_request_and_plan() -> None:
    request, plan = request_and_plan(_sess("copilot"), [])
    assert request == "" and plan == []
