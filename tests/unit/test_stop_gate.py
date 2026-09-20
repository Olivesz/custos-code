"""The Stop hook must not pay for a model call on a turn that cannot produce a verdict.

Stop fires on every turn. Before this gate, a turn that ran a single `rm` triggered a full
session review -- and in auto mode up to three of them. On 2026-09-19 that made a one-line
command take tens of seconds, and the reasonable conclusion was that the terminal was broken.
A checker nobody leaves switched on verifies nothing, so cost here is a correctness concern.
"""
from __future__ import annotations

import pathlib

import pytest

import custos_code.hooks as h
from custos_code.models import EventFlags, EventKind, LedgerEvent


def _live(sid: str, n_calls: int) -> None:
    live, _, _ = h._paths(sid)
    rows = []
    for i in range(n_calls):
        rows.append(LedgerEvent(seq=2 * i, ts="2026-09-19T00:00:00Z", session_id=sid,
                                kind=EventKind.CALL, tool="Bash",
                                input={"command": f"cmd-{i}"}, flags=EventFlags()))
        rows.append(LedgerEvent(seq=2 * i + 1, ts="2026-09-19T00:00:00Z", session_id=sid,
                                kind=EventKind.RESULT, tool="Bash", output="ok", flags=EventFlags()))
    with open(live, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(r.model_dump_json() + "\n")


@pytest.fixture(autouse=True)
def _home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(h, "HOME", str(tmp_path))


def _boom(*a: object, **k: object) -> object:
    raise AssertionError("a model backend was constructed; the gate should have returned first")


def test_no_tool_calls_means_no_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A purely conversational turn: every claim would be unwitnessed, which never blocks."""
    sid = "s-empty"
    _live(sid, 0)
    monkeypatch.setattr(h.judge_mod, "make_backend", _boom)
    assert h.on_stop({"session_id": sid, "last_assistant_message": "Sure, here is my view.",
                      "cwd": "/x"}) is None


def test_second_turn_with_no_new_evidence_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ledger has not moved, so no verdict can have moved either."""
    sid = "s-repeat"
    _live(sid, 2)
    calls = {"n": 0}

    def counting(*a: object, **k: object) -> None:
        calls["n"] += 1
        return None          # no backend -> rules path; we only care that we got this far

    monkeypatch.setattr(h.judge_mod, "make_backend", counting)
    h.on_stop({"session_id": sid, "last_assistant_message": "Ran the tests.", "cwd": "/x"})
    assert calls["n"] == 1, "first turn should review"
    h.on_stop({"session_id": sid, "last_assistant_message": "Ran the tests.", "cwd": "/x"})
    assert calls["n"] == 1, "second turn with an unchanged ledger must not review again"


def test_new_evidence_reopens_the_review(monkeypatch: pytest.MonkeyPatch) -> None:
    sid = "s-grow"
    _live(sid, 1)
    calls = {"n": 0}

    def counting(*a: object, **k: object) -> None:
        calls["n"] += 1
        return None

    monkeypatch.setattr(h.judge_mod, "make_backend", counting)
    h.on_stop({"session_id": sid, "last_assistant_message": "one", "cwd": "/x"})
    _live(sid, 3)                       # agent ran more tools
    h.on_stop({"session_id": sid, "last_assistant_message": "two", "cwd": "/x"})
    assert calls["n"] == 2, "a grown ledger must be reviewed again"


def test_auto_mode_continuation_is_never_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """stop_hook_active means we are inside a correction pass; it must always re-check."""
    sid = "s-cont"
    _live(sid, 2)
    calls = {"n": 0}

    def counting(*a: object, **k: object) -> None:
        calls["n"] += 1
        return None

    monkeypatch.setattr(h.judge_mod, "make_backend", counting)
    h.on_stop({"session_id": sid, "last_assistant_message": "done", "cwd": "/x"})
    h.on_stop({"session_id": sid, "last_assistant_message": "done", "cwd": "/x",
               "stop_hook_active": True})
    assert calls["n"] == 2, "a correction pass must re-check even with an unchanged ledger"
