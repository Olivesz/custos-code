"""The Stop hook must read the live hook file, not the transcript.

Found on a real session (adc885ec, 2026-09-19): `_ledger_for` preferred the transcript whenever
`transcript_path` was present, which is always. That discarded the full tool output PostToolUse
exists to capture (28,437 bytes live vs 20,424 in the transcript on that session) and renumbered
every event, so the receipt's citations pointed at different lines than the ledger on disk.
"""
from __future__ import annotations

import json
import pathlib

from custos_code.hooks import _ledger_for, _paths
from custos_code.models import EventFlags, EventKind, LedgerEvent


def _write_live(sid: str, n: int) -> str:
    live, _, _ = _paths(sid)
    rows = []
    for i in range(n):
        rows.append(LedgerEvent(seq=2 * i, ts="2026-09-19T00:00:00Z", session_id=sid,
                                kind=EventKind.CALL, tool="Bash",
                                input={"command": f"cmd-{i}"}, flags=EventFlags()))
        rows.append(LedgerEvent(seq=2 * i + 1, ts="2026-09-19T00:00:00Z", session_id=sid,
                                kind=EventKind.RESULT, tool="Bash",
                                output="X" * 5000, flags=EventFlags()))
    with open(live, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(r.model_dump_json() + "\n")
    return live


def test_live_file_wins_over_transcript(tmp_path: pathlib.Path, monkeypatch) -> None:
    import custos_code.hooks as h
    monkeypatch.setattr(h, "HOME", str(tmp_path))
    sid = "sess-live-wins"
    _write_live(sid, 3)
    # A transcript that PARSES TO REAL EVENTS -- otherwise transcript-first would fall through to
    # the live file anyway and this test would pass against the bug it is meant to catch.
    t = tmp_path / "transcript.jsonl"
    rows = [
        {"type": "assistant", "sessionId": sid, "cwd": "/x", "uuid": f"u{i}",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": f"t{i}", "name": "Bash",
              "input": {"command": f"transcript-cmd-{i}"}}]}}
        for i in range(9)
    ]
    t.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    sess, ledger = _ledger_for({"session_id": sid, "transcript_path": str(t), "cwd": "/x"})
    assert len(ledger) == 6, f"expected the 6 live events, got {len(ledger)}"
    cmds = [e.input["command"] for e in ledger if e.kind == EventKind.CALL]
    assert cmds == ["cmd-0", "cmd-1", "cmd-2"], cmds
    # the full 5000-byte outputs survive; the transcript would have had none of this
    assert sum(len(e.output or "") for e in ledger) == 15000


def test_transcript_is_used_only_when_there_is_no_live_file(tmp_path: pathlib.Path, monkeypatch) -> None:
    import custos_code.hooks as h
    monkeypatch.setattr(h, "HOME", str(tmp_path))
    sid = "sess-no-live"
    t = tmp_path / "transcript.jsonl"
    rec = {"type": "assistant", "sessionId": sid, "cwd": "/x", "uuid": "u1",
           "message": {"role": "assistant", "content": [
               {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo hi"}}]}}
    t.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    _, ledger = _ledger_for({"session_id": sid, "transcript_path": str(t), "cwd": "/x"})
    assert any(e.kind == EventKind.CALL for e in ledger), "transcript fallback did not parse"


def test_citations_match_the_file_a_reader_would_open(tmp_path: pathlib.Path, monkeypatch) -> None:
    """Every seq the receipt can cite must exist at that seq in ~/.custos-code/live/<id>.jsonl."""
    import custos_code.hooks as h
    monkeypatch.setattr(h, "HOME", str(tmp_path))
    sid = "sess-citation"
    live = _write_live(sid, 4)
    _, ledger = _ledger_for({"session_id": sid, "transcript_path": "", "cwd": "/x"})
    with open(live, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    on_disk = {r["seq"]: r for r in rows}
    for e in ledger:
        assert e.seq in on_disk, f"receipt could cite #{e.seq}, absent from the ledger on disk"
        assert on_disk[e.seq]["kind"] == e.kind.value
