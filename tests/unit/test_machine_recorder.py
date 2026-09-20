"""Class M: a shell trap knows status, not stdout, and never who ran the command."""

import json
import os
import subprocess

import pytest

from custos_code.adapters import machine
from custos_code.ledger import verify_chain
from custos_code.models import EventKind


def _log(tmp_path, rows: list[dict]) -> str:
    path = tmp_path / "host-2025-09-19.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(path)


def _row(**kw) -> dict:
    return {"recorder": "custos-code-machine", "v": 1, "cwd": "/srv/app", "pid": 100, **kw}


def test_record_line_is_one_wire_object_with_status_and_duration() -> None:
    line = json.loads(
        machine.record_line(
            {
                "CUSTOS_CODE_EVENT": "end",
                "CUSTOS_CODE_CMD": "pytest -q",
                "CUSTOS_CODE_RC": "1",
                "CUSTOS_CODE_T0": "1000.0",
                "CUSTOS_CODE_TS": "1002.5",
                "PWD": "/srv/app",
                "CUSTOS_CODE_PID": "42",
            }
        )
    )
    assert line["recorder"] == "custos-code-machine" and line["v"] == machine.WIRE_VERSION
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
        {"CUSTOS_CODE_EVENT": "start", "CUSTOS_CODE_CMD": "deploy --key sk-abcdefghijklmnopqrstuvwx"}
    )
    assert "sk-abcdefghijklmnopqrstuvwx" not in line


@pytest.mark.parametrize("recorder", ["custos-code-machine", "receipts-machine"])
def test_parse_pairs_start_and_end_and_keeps_the_exit_code(tmp_path, recorder) -> None:
    path = _log(
        tmp_path,
        [
            _row(recorder=recorder, event="start", ts=1_758_276_000.0, cmd="pytest -q | tail -1"),
            _row(recorder=recorder, event="end", ts=1_758_276_002.5,
                 cmd="pytest -q | tail -1", exit=1, dur_ms=2500),
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


def test_no_cross_ledger_merge_is_exposed() -> None:
    # borrowing exit codes from this log into a harness ledger would need a re-chain and a marker
    # on every borrowed row (invariant 1); until that exists the join is deliberately absent
    assert not hasattr(machine, "merge_exit_codes")


def test_record_line_falls_back_to_the_calling_shell_for_pid() -> None:
    line = json.loads(machine.record_line({"CUSTOS_CODE_EVENT": "start", "CUSTOS_CODE_CMD": "ls"}))
    assert line["pid"] == os.getppid() and line["ppid"] is None


def test_pairing_survives_a_repeated_command(tmp_path) -> None:
    rows = [
        _row(event="start", ts=1.0, cmd="pytest -q", pid=100),
        _row(event="end", ts=2.0, cmd="pytest -q", pid=100, exit=0),
        _row(event="start", ts=3.0, cmd="pytest -q", pid=100),
        _row(event="end", ts=4.0, cmd="pytest -q", pid=100, exit=1),
    ]
    _, ledger, _ = machine.parse(_log(tmp_path, rows))
    assert [e.exit_code for e in ledger if e.kind is EventKind.RESULT] == [0, 1]


def test_snippets_exist_for_supported_shells_only() -> None:
    assert "custos-code _record-line" in machine.install_snippet("bash")
    assert "add-zsh-hook" in machine.install_snippet("zsh")
    with pytest.raises(ValueError, match="no recorder snippet"):
        machine.install_snippet("fish")


def test_bash_snippet_is_syntactically_valid() -> None:
    proc = subprocess.run(
        ["bash", "-n"], input=machine.install_snippet("bash"), text=True, capture_output=True
    )
    assert proc.returncode == 0, proc.stderr


def test_bash_snippet_does_not_word_split_the_command() -> None:
    # CUSTOS_CODE_CMD=$__CUSTOS_CODE_CMD unquoted would record `pytest` and drop `-q` into argv
    script = machine.install_snippet("bash").replace("custos-code _record-line", "env")
    proc = subprocess.run(
        ["bash", "-c", f'{script}\n__CUSTOS_CODE_CMD="pytest -q"; __custos_code_precmd'],
        text=True,
        capture_output=True,
        env={**os.environ, "CUSTOS_CODE_MACHINE_LOG": "/dev/stdout"},
    )
    assert "CUSTOS_CODE_CMD=pytest -q" in proc.stdout, proc.stdout or proc.stderr
