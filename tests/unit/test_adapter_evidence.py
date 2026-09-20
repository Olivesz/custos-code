"""Evidence the transcript carries but the adapter used to discard.

Both of these made whole classes of claim unverifiable by construction, which is worse than a
wrong answer: the checker could not have got them right no matter how good the judge was.

  - `exit_code` was hardcoded `None`, so `review._corroborate`'s "a cited event shows a non-zero
    exit" path could never fire on this adapter, and `rules._outcome` could never contradict on
    exit status. Measured 2026-09-20: 174 non-zero exits recoverable across 58 real sessions.
  - Edit results rendered as "The file ... has been updated successfully." and nothing else, so
    any claim about *what* an edit changed was unwitnessable. 1611 such results in the same
    sample; 72% carry a `structuredPatch` we were throwing away.
"""
from __future__ import annotations

import json
import pathlib

from custos_code.adapters import claude_code
from custos_code.models import EventKind

SID = "11111111-2222-3333-4444-555555555555"


def _write(tmp_path: pathlib.Path, *results: dict) -> str:
    """A minimal main-chain transcript: one call per result, then a final assistant message."""
    rows: list[dict] = []
    for i, tur in enumerate(results):
        tid = f"toolu_{i}"
        rows.append({"type": "assistant", "sessionId": SID, "cwd": "/repo",
                     "timestamp": "2026-09-20T00:00:00Z", "message": {"content": [
                         {"type": "tool_use", "id": tid, "name": tur.pop("_tool"),
                          "input": tur.pop("_input")}]}})
        rows.append({"type": "user", "sessionId": SID, "cwd": "/repo",
                     "timestamp": "2026-09-20T00:00:01Z",
                     "toolUseResult": tur.pop("_tur", None),
                     "message": {"content": [dict(tur, type="tool_result", tool_use_id=tid)]}})
    rows.append({"type": "assistant", "sessionId": SID, "cwd": "/repo",
                 "timestamp": "2026-09-20T00:00:02Z",
                 "message": {"content": [{"type": "text", "text": "Done."}]}})
    p = tmp_path / "session.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return str(p)


def test_a_stated_exit_code_is_recorded(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, {"_tool": "Bash", "_input": {"command": "pytest"},
                             "content": "Exit code 1\nFAILED tests/test_x.py", "is_error": True})
    res = [e for _, led, _ in [claude_code.parse(path)] for e in led if e.kind is EventKind.RESULT]
    assert res[0].exit_code == 1
    assert res[0].flags.error


def test_success_is_never_inferred(tmp_path: pathlib.Path) -> None:
    """No `is_error` means the harness did not flag it -- not that the process returned 0.

    Promoting that absence to a witnessed 0 is precisely the move this project marks
    `unwitnessed`, and `rules._outcome` treats a real 0 as grounds to confirm.
    """
    path = _write(tmp_path, {"_tool": "Bash", "_input": {"command": "pytest"},
                             "content": "2 passed in 0.1s"})
    res = [e for _, led, _ in [claude_code.parse(path)] for e in led if e.kind is EventKind.RESULT]
    assert res[0].exit_code is None


def test_an_error_without_a_stated_code_stays_unknown(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, {"_tool": "Bash", "_input": {"command": "pytest"},
                             "content": "something went wrong", "is_error": True})
    res = [e for _, led, _ in [claude_code.parse(path)] for e in led if e.kind is EventKind.RESULT]
    assert res[0].exit_code is None and res[0].flags.error


def test_an_edit_carries_the_lines_it_changed(tmp_path: pathlib.Path) -> None:
    patch = [{"oldStart": 3, "oldLines": 1, "newStart": 3, "newLines": 2,
              "lines": [" keep", "-TIMEOUT = 5", "+TIMEOUT = 30"]}]
    path = _write(tmp_path, {"_tool": "Edit", "_input": {"file_path": "/repo/a.py"},
                             "content": "The file /repo/a.py has been updated successfully.",
                             "_tur": {"filePath": "/repo/a.py", "structuredPatch": patch}})
    res = [e for _, led, _ in [claude_code.parse(path)] for e in led if e.kind is EventKind.RESULT]
    out = res[0].output or ""
    assert "-TIMEOUT = 5" in out and "+TIMEOUT = 30" in out, "the judge still cannot see the edit"
    assert "@@ -3,1 +3,2 @@" in out


def test_a_diff_is_redacted_like_any_other_recorded_content(tmp_path: pathlib.Path) -> None:
    """A patch is file contents, so it can carry a secret the ledger must not keep verbatim."""
    secret = "sk-" + "a" * 48
    patch = [{"oldStart": 1, "oldLines": 0, "newStart": 1, "newLines": 1,
              "lines": [f"+API_KEY = '{secret}'"]}]
    path = _write(tmp_path, {"_tool": "Write", "_input": {"file_path": "/repo/b.py"},
                             "content": "ok", "_tur": {"structuredPatch": patch}})
    res = [e for _, led, _ in [claude_code.parse(path)] for e in led if e.kind is EventKind.RESULT]
    assert secret not in (res[0].output or ""), "a secret was written into the ledger verbatim"
