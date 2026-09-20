"""Our own instrumentation must not become evidence against the agent.

Session 21756df4, 2026-09-19. `on_pre_tool_use` rewrites a runner command to capture the resolved
binary, and the rewrite contains `command -v <bin> 2>/dev/null`. PostToolUse then recorded OUR
command instead of the agent's, so:

  * `_PIPE_RE` matched our `2>/dev/null` and flagged the call `piped`, i.e. "output filtered" --
    five true claims came back `unrecorded` for filtering that we had done, with rationales that
    actually confirmed them ("git status --short shows the rename exactly as stated");
  * the receipt quoted a command line the agent never typed.

The agent noticed and said so in its final report. It was right. A checker that manufactures the
evidence it then judges is worse than no checker.
"""
from __future__ import annotations

import itertools
import json
import pathlib

import pytest

import receipts.hooks as h
from receipts.models import EventKind, LedgerEvent


@pytest.fixture(autouse=True)
def _home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(h, "HOME", str(tmp_path))
    monkeypatch.delenv("RECEIPTS_ONLY_IN", raising=False)


ORIGINAL = "pytest -q tests/checks.py"


_N = itertools.count()


def _round_trip(original: str) -> tuple[LedgerEvent, LedgerEvent]:
    """Drive pre -> post exactly as Claude Code does, and return the recorded CALL event."""
    n = next(_N)
    sid, tuid = f"s-wrap-{n}", f"toolu_{n}"      # a fresh ledger per call, or tests share events
    pre = h.on_pre_tool_use({"session_id": sid, "tool_name": "Bash", "tool_use_id": tuid,
                             "tool_input": {"command": original}, "cwd": "/x"})
    rewritten = original
    if pre is not None:
        rewritten = pre["hookSpecificOutput"]["updatedInput"]["command"]
        assert rewritten != original, "precondition: this command should have been wrapped"
    h.on_post_tool_use({"session_id": sid, "tool_name": "Bash", "tool_use_id": tuid,
                        "tool_input": {"command": rewritten},
                        "tool_response": "1 error in 0.04s\nexit=2", "cwd": "/x"})
    live, _, _ = h._paths(sid)
    events = [LedgerEvent.model_validate_json(x) for x in open(live) if x.strip()]
    calls = [e for e in events if e.kind == EventKind.CALL]
    results = [e for e in events if e.kind == EventKind.RESULT]
    assert calls and results
    # the piped/truncated flags live on the RESULT, which is what _veto and annotate read
    return calls[0], results[0]


def test_the_ledger_records_the_agents_command_not_ours() -> None:
    call, _ = _round_trip(ORIGINAL)
    cmd = (call.input or {}).get("command")
    assert cmd == ORIGINAL, f"receipt would quote a command the agent never ran: {cmd!r}"
    assert "RECEIPTS_RC_FILE" not in str(cmd)


def test_our_wrapper_does_not_flag_the_call_as_filtered() -> None:
    """The bug: our `2>/dev/null` matched the output-filtered detector."""
    _, result = _round_trip(ORIGINAL)
    assert result.flags.piped is False, "our own instrumentation marked the agent's output filtered"


def test_a_genuinely_piped_command_is_still_flagged() -> None:
    """The guard must not become a way to launder a real pipe."""
    call, result = _round_trip("pytest -q | tail -5")
    assert (call.input or {}).get("command") == "pytest -q | tail -5"
    assert result.flags.piped is True, "a real `| tail` must still be flagged"


def test_legacy_bare_path_pending_entries_still_work(tmp_path: pathlib.Path) -> None:
    rc, cmd = h._unpack_pending("/some/rc/path")
    assert rc == "/some/rc/path" and cmd is None
    rc2, cmd2 = h._unpack_pending(json.dumps({"rc": "/p", "cmd": "pytest -q"}))
    assert rc2 == "/p" and cmd2 == "pytest -q"
