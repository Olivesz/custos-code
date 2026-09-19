"""Class M: a shell trap knows status, not stdout, and never who ran the command."""

import json
import subprocess
from datetime import UTC, datetime

import pytest

from receipts.adapters import machine
from receipts.ledger import verify_chain
from receipts.models import EventFlags, EventKind, LedgerEvent


def _log(tmp_path, rows: list[dict]) -> str:
    path = tmp_path / "host-2025-09-19.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(path)


def _row(**kw) -> dict:
    return {"recorder": "receipts-machine", "v": 1, "cwd": "/srv/app", "pid": 100, **kw}


def test_record_line_is_one_wire_object_with_status_and_duration() -> None:
    line = json.loads(
        machine.record_line(
            {
                "RECEIPTS_EVENT": "end",
                "RECEIPTS_CMD": "pytest -q",
                "RECEIPTS_RC": "1",
                "RECEIPTS_T0": "1000.0",
                "RECEIPTS_TS": "1002.5",
                "PWD": "/srv/app",
                "RECEIPTS_PID": "42",
            }
        )
    )
    assert line["recorder"] == "receipts-machine" and line["v"] == machine.WIRE_VERSION
    assert line == {
        **line,
        "event": "end",
        "cmd": "pytest -q",
        "exit": 1,
        "dur_ms": 2500,
        "cwd": "/srv/app",
        "pid": 42,
    }


def test_record_line_redacts_secrets_in_the_command() -> None:
    line = machine.record_line(
        {"RECEIPTS_EVENT": "start", "RECEIPTS_CMD": "deploy --key sk-abcdefghijklmnopqrstuvwx"}
    )
    assert "sk-abcdefghijklmnopqrstuvwx" not in line


def test_parse_pairs_start_and_end_and_keeps_the_exit_code(tmp_path) -> None:
    path = _log(
        tmp_path,
        [
            _row(event="start", ts=1_758_276_000.0, cmd="pytest -q | tail -1"),
            _row(event="end", ts=1_758_276_002.5, cmd="pytest -q | tail -1", exit=1, dur_ms=2500),
        ],
    )
    sess, ledger, report = machine.parse(path)
    assert report is None  # class M has no report; it comes from the PR or the UI
    assert sess.source == "machine" and sess.integrity_score == 0.6
    assert sess.agent.startswith("unknown (")  # attribution is a hint, never a claim
    assert verify_chain(ledger)
    call, result = ledger
    assert call.kind is EventKind.CALL and call.flags.piped
    assert result.exit_code == 1 and result.flags.error and result.duration_ms == 2500
    # the trap sees no stdout: outcome known, output known-missing
    assert result.flags.stderr_dropped and result.output is None


def test_fs_and_git_rows_become_state_evidence(tmp_path) -> None:
    path = _log(
        tmp_path,
        [
            _row(event="fs", ts=1_758_276_010.0, path="src/app.py", op="modified"),
            _row(
                event="git",
                ts=1_758_276_020.0,
                ref="HEAD",
                **{"from": "aaa", "to": "bbb"},
                subject="feat: x",
            ),
        ],
    )
    _, ledger, _ = machine.parse(path)
    edit, git = ledger
    assert edit.tool == "Edit" and edit.paths == ["/srv/app/src/app.py"] and edit.exit_code == 0
    assert git.tool == "Git" and (git.input or {})["to"] == "bbb"


def test_foreign_lines_are_ignored(tmp_path) -> None:
    path = _log(
        tmp_path,
        [
            {"recorder": "something-else", "event": "end"},
            _row(event="end", ts=1.0, cmd="ls", exit=0),
        ],
    )
    _, ledger, _ = machine.parse(path)
    assert len(ledger) == 1


def _result(seq: int, command: str, ts: float, exit_code: int | None) -> LedgerEvent:
    return LedgerEvent(
        seq=seq,
        session_id="s",
        kind=EventKind.RESULT,
        tool="Bash",
        ts=datetime.fromtimestamp(ts, tz=UTC),
        input={"command": command},
        exit_code=exit_code,
        flags=EventFlags(),
    )


def test_merge_only_fills_gaps_and_only_when_the_match_is_unambiguous() -> None:
    harness = [
        _result(0, "pytest -q", 1000.0, None),  # gap: one machine row nearby
        _result(1, "make build", 2000.0, 0),  # already known: never overwritten
        _result(2, "ruff check", 3000.0, None),
    ]  # two candidates: too ambiguous to join
    machine_rows = [
        _result(0, "pytest -q", 1001.0, 1),
        _result(1, "make build", 2001.0, 9),
        _result(2, "ruff check", 3001.0, 0),
        _result(3, "ruff check", 3002.0, 1),
    ]
    assert machine.merge_exit_codes(harness, machine_rows) == 1
    assert harness[0].exit_code == 1 and harness[0].flags.error
    assert harness[1].exit_code == 0
    assert harness[2].exit_code is None


def test_merge_respects_the_time_window() -> None:
    harness = [_result(0, "pytest -q", 1000.0, None)]
    assert machine.merge_exit_codes(harness, [_result(0, "pytest -q", 1100.0, 1)]) == 0
    assert harness[0].exit_code is None


def test_snippets_exist_for_supported_shells_only() -> None:
    assert "receipts _record-line" in machine.install_snippet("bash")
    assert "add-zsh-hook" in machine.install_snippet("zsh")
    with pytest.raises(ValueError, match="no recorder snippet"):
        machine.install_snippet("fish")


def test_bash_snippet_is_syntactically_valid() -> None:
    proc = subprocess.run(
        ["bash", "-n"], input=machine.install_snippet("bash"), text=True, capture_output=True
    )
    assert proc.returncode == 0, proc.stderr
