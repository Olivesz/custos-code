"""A Tier 3 result has to reach a verdict, or the re-run is theatre.

`spawn_async` detaches and writes a RERUN event to a result file. Claude Code hooks are one-shot,
so the Stop call that launched it has already returned -- something must pick the result up on a
LATER turn. Nothing did: `load_result` had no callers outside its own module. The subprocess ran,
the evidence landed on disk, and the checker never looked.

Caught by an adversarial pass before it shipped, which is why the consumption path gets its own
tests rather than being assumed from the spawn ones.
"""
from __future__ import annotations

import json
import pathlib

import pytest

import custos_code.hooks as h
import custos_code.rerun as rerun
from custos_code.models import EventFlags, EventKind, LedgerEvent


@pytest.fixture(autouse=True)
def _home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(h, "HOME", str(tmp_path / ".receipts"))
    monkeypatch.setenv("HOME", str(tmp_path))       # rerun._rerun_dir uses Path.home()
    monkeypatch.delenv("CUSTOS_CODE_ONLY_IN", raising=False)


def _write_result(sid: str, claim_id: str, output: str) -> None:
    d = rerun._rerun_dir(sid)
    d.mkdir(parents=True, exist_ok=True)
    ev = LedgerEvent(seq=0, ts="2026-09-20T00:00:00Z", session_id=sid, kind=EventKind.RERUN,
                     tool="pytest", output=output, exit_code=0, flags=EventFlags())
    (d / f"{claim_id}.result.json").write_text(ev.model_dump_json(), encoding="utf-8")


def _live(sid: str, rows: list[dict]) -> str:
    live, _, _ = h._paths(sid)
    with open(live, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return live


CALL = {"seq": 0, "ts": "2026-09-20T00:00:00Z", "session_id": "s", "kind": "call", "tool": "Bash",
        "input": {"command": "pytest -q > /dev/null"}, "flags": {}}
RESULT = {"seq": 1, "ts": "2026-09-20T00:00:00Z", "session_id": "s", "kind": "result",
          "tool": "Bash", "output": "", "flags": {"piped": True}}


def test_a_finished_rerun_enters_the_ledger() -> None:
    live = _live("s", [CALL, RESULT])
    _write_result("s", "c1", "3 passed in 0.10s")
    led = h._collect_reruns("s", [LedgerEvent.model_validate_json(x) for x in open(live) if x.strip()], live)
    reruns = [e for e in led if e.kind == EventKind.RERUN]
    assert len(reruns) == 1, "the re-run result never reached the ledger"
    assert "3 passed" in (reruns[0].output or "")


def test_it_is_consumed_exactly_once() -> None:
    """Otherwise every later turn re-ingests it and the ledger grows without bound."""
    live = _live("s", [CALL, RESULT])
    _write_result("s", "c1", "3 passed")
    base = [LedgerEvent.model_validate_json(x) for x in open(live) if x.strip()]
    h._collect_reruns("s", list(base), live)
    again = [LedgerEvent.model_validate_json(x) for x in open(live) if x.strip()]
    led2 = h._collect_reruns("s", again, live)
    assert sum(1 for e in led2 if e.kind == EventKind.RERUN) == 1


def test_the_folded_event_gets_a_real_seq() -> None:
    """`run_worker` writes seq=0 as a placeholder (NEEDS-DECISION(oliver)); only the ledger's
    owner knows the next number, so it is assigned here. A duplicate seq makes every citation in
    the receipt ambiguous."""
    live = _live("s", [CALL, RESULT])
    _write_result("s", "c1", "3 passed")
    led = h._collect_reruns("s", [LedgerEvent.model_validate_json(x) for x in open(live) if x.strip()], live)
    seqs = [e.seq for e in led]
    assert len(seqs) == len(set(seqs)), f"duplicate seq after folding: {seqs}"
    assert max(seqs) == 2


def test_it_does_not_invent_a_live_file_and_clobber_a_transcript() -> None:
    """`_ledger_for` prefers the live file whenever it has ANY events. Creating one that holds
    only RERUN events would make the next turn discard the transcript it had been using."""
    live, _, _ = h._paths("s2")
    assert not pathlib.Path(live).exists()
    _write_result("s2", "c1", "3 passed")
    led = h._collect_reruns("s2", [], live)
    assert len(led) == 1, "the result should still be visible to THIS turn"
    assert not pathlib.Path(live).exists(), "a live ledger was invented from a transcript session"


def test_a_missing_or_corrupt_result_does_not_take_the_turn_down() -> None:
    live = _live("s", [CALL, RESULT])
    d = rerun._rerun_dir("s")
    d.mkdir(parents=True, exist_ok=True)
    (d / "c9.result.json").write_text("{not json", encoding="utf-8")
    led = h._collect_reruns("s", [LedgerEvent.model_validate_json(x) for x in open(live) if x.strip()], live)
    assert len(led) == 2, "a corrupt result file must be skipped, not fatal"


def test_no_rerun_directory_is_not_an_error() -> None:
    live = _live("s3", [CALL, RESULT])
    led = h._collect_reruns("s3", [LedgerEvent.model_validate_json(x) for x in open(live) if x.strip()], live)
    assert len(led) == 2


def test_on_stop_actually_calls_the_collector(monkeypatch: pytest.MonkeyPatch,
                                              tmp_path: pathlib.Path) -> None:
    """The tests above exercise `_collect_reruns` directly, so they pass even if nothing calls it.

    That is precisely the bug this whole file exists for -- a correct function that is never
    invoked looks identical to a working feature. This one drives the real entry point.
    """
    live = _live("s4", [dict(CALL, session_id="s4"), dict(RESULT, session_id="s4")])
    _write_result("s4", "c1", "3 passed in 0.10s")

    # no backend: the rules path runs, which is enough to prove the ledger was assembled
    monkeypatch.setattr(h.judge_mod, "make_backend", lambda *a, **k: None)
    h.on_stop({"session_id": "s4", "cwd": str(tmp_path),
               "last_assistant_message": "Ran the suite, all tests passing."})

    rows = [json.loads(x) for x in open(live) if x.strip()]
    assert any(r["kind"] == "rerun" for r in rows), \
        "on_stop did not fold the Tier 3 result into the ledger"
