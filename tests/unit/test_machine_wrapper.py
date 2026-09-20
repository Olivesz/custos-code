"""docs/ADAPTERS.md §4/§7: the PATH-first wrapper for non-interactive, agent-spawned shells.

`bash -c "cmd"`/`sh -c "cmd"` (what Claude Code and Codex actually spawn) is neither interactive
nor a login shell, so it never sources ~/.bashrc -- install_snippet's DEBUG-trap/preexec hooks
never attach. These tests exercise the actual generated wrapper script as a real subprocess, not
just the Python that renders it, since the whole point is shell behavior (argv shape, exit status,
stdio passthrough) that a unit test of the template string alone couldn't catch.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from receipts.adapters import machine

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs a real bash")


def _receipts_on_path(env: dict[str, str]) -> dict[str, str]:
    """The wrapper shells out to `receipts _record-line`; make sure it can find this checkout's."""
    receipts_bin = shutil.which("receipts")
    if receipts_bin:
        env = dict(env)
        env["PATH"] = os.path.dirname(receipts_bin) + os.pathsep + env["PATH"]
    return env


def test_install_wrapper_writes_executable_scripts(tmp_path: Path) -> None:
    written = machine.install_wrapper(str(tmp_path))
    assert set(written) == {"bash", "sh"}
    for path in written.values():
        assert os.access(path, os.X_OK)
        assert "receipts recorder" in Path(path).read_text()


def test_install_wrapper_refuses_to_wrap_itself(tmp_path: Path) -> None:
    """If `which` resolves inside the target dir (i.e. we'd only ever call ourselves), refuse
    rather than install an infinite loop."""
    with pytest.raises(FileNotFoundError):
        machine.install_wrapper(str(tmp_path), which=lambda name: str(tmp_path / name))


def test_install_wrapper_refuses_when_no_real_shell_exists(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        machine.install_wrapper(str(tmp_path), which=lambda name: None)


def test_wrapped_bash_preserves_exit_code_and_stdio(tmp_path: Path) -> None:
    written = machine.install_wrapper(str(tmp_path))
    env = _receipts_on_path(dict(os.environ))
    env["RECEIPTS_MACHINE_LOG"] = str(tmp_path / "log.jsonl")
    r = subprocess.run([written["bash"], "-c", "echo hi; echo err >&2; exit 7"],
                       env=env, capture_output=True, text=True, timeout=15)
    assert r.returncode == 7
    assert r.stdout == "hi\n"
    assert r.stderr == "err\n"


def test_wrapped_bash_logs_the_dash_c_command_not_the_flag(tmp_path: Path) -> None:
    written = machine.install_wrapper(str(tmp_path))
    log = tmp_path / "log.jsonl"
    env = _receipts_on_path(dict(os.environ))
    env["RECEIPTS_MACHINE_LOG"] = str(log)
    subprocess.run([written["bash"], "-c", "true"], env=env, capture_output=True, text=True, timeout=15)
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r["event"] for r in rows] == ["start", "end"]
    assert all(r["cmd"] == "true" for r in rows)  # not "-c true", not "-c"
    assert rows[1]["exit"] == 0


def test_wrapped_bash_round_trips_through_machine_parse(tmp_path: Path) -> None:
    written = machine.install_wrapper(str(tmp_path))
    log = tmp_path / "log.jsonl"
    env = _receipts_on_path(dict(os.environ))
    env["RECEIPTS_MACHINE_LOG"] = str(log)
    subprocess.run([written["bash"], "-c", "echo from-wrapper"], env=env,
                   capture_output=True, text=True, timeout=15)
    sess, ledger, report = machine.parse(str(log))
    assert report is None
    calls = [e for e in ledger if e.kind.value == "call"]
    results = [e for e in ledger if e.kind.value == "result"]
    assert len(calls) == 1 and len(results) == 1
    assert calls[0].input is not None and calls[0].input.get("command") == "echo from-wrapper"
    assert results[0].exit_code == 0
    assert sess.source == "machine"


def test_wrapped_sh_also_works(tmp_path: Path) -> None:
    written = machine.install_wrapper(str(tmp_path))
    env = _receipts_on_path(dict(os.environ))
    env["RECEIPTS_MACHINE_LOG"] = str(tmp_path / "log.jsonl")
    r = subprocess.run([written["sh"], "-c", "exit 5"], env=env, capture_output=True, text=True, timeout=15)
    assert r.returncode == 5
