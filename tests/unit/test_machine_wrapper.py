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
    """The wrapper shells out to `receipts _record-line`; make sure it can find this checkout's.

    When `receipts` isn't resolvable at all (this checkout's `.venv/bin` not on the test runner's
    own PATH), the wrapper's `|| true` swallows that silently and the test would see an empty log
    and fail on an assertion that looks unrelated to the real cause. Skip instead, with the real
    reason -- the wrapper's own silent-no-op-on-a-broken-`receipts` behavior is itself a gap
    worth having a name for, not something a test failure should stand in for.
    """
    receipts_bin = shutil.which("receipts")
    if not receipts_bin:
        pytest.skip("no `receipts` console script on PATH; can't exercise the wrapper's own call to it")
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


def test_wrapper_dir_first_on_path_does_not_recurse(tmp_path: Path) -> None:
    """The one configuration `--wrapper --install` actually ships: the wrapper directory ahead of
    the system one on PATH, so a bare, unqualified `bash -c "cmd"` -- exactly how an agent spawns
    it, never by the wrapper's own absolute path -- resolves to the wrapper first.

    A `#!/usr/bin/env bash` shebang re-resolves "bash" through that same PATH and finds the
    wrapper again, whose shebang does the same thing, forever -- `echo hi` never runs, and every
    `#!/bin/sh` script and `subprocess(shell=True)` on the machine burns CPU and fails as long as
    the wrapper dir leads PATH. The other tests here invoke the wrapper by its absolute path,
    which can't catch this: `env` never gets a chance to re-resolve anything. This is the
    configuration the earlier `#!/usr/bin/env bash` shebang bricked.
    """
    written = machine.install_wrapper(str(tmp_path))
    env = _receipts_on_path(dict(os.environ))
    env["RECEIPTS_MACHINE_LOG"] = str(tmp_path / "log.jsonl")
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    r = subprocess.run(["bash", "-c", "echo hi; exit 3"], env=env,
                       capture_output=True, text=True, timeout=15)
    assert written["bash"]  # sanity: the wrapper we just installed is the one PATH now finds first
    assert r.returncode == 3
    assert r.stdout == "hi\n"
