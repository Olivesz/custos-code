"""The hooks must resolve without PATH, and must never block because they failed to start.

Both regressions were live on 2026-09-19: `uv` was not installed on the machine, every
hooks/*.sh hardcoded `uv run`, and stop.sh ran under `set -e` so the missing binary became
`exit 2` -- which Claude Code reads as "block this turn". A broken install blocked every turn
with no claim behind it. That is a false accusation with no evidence, which AGENTS.md forbids.
"""
from __future__ import annotations

import json
import os
import pathlib
import shlex
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from custos_code.cli import _hook_command, app, hooks_snippet

HOOKS = pathlib.Path(__file__).resolve().parents[2] / "hooks"
BASH = shutil.which("bash") or "/bin/bash"  # resolved before any test empties PATH
EVENTS = ["pre", "post-tool-use", "stop"]
SCRIPTS = {"pre": "pre_tool_use.sh", "post-tool-use": "post_tool_use.sh", "stop": "stop.sh"}


def _run(script: str, payload: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, str(HOOKS / script)], input=payload, capture_output=True, text=True, env=env, timeout=120)


@pytest.mark.parametrize("event", EVENTS)
def test_hook_command_is_absolute_and_needs_no_path(event: str) -> None:
    """Claude Code runs hooks in a shell that does not inherit our PATH.

    `_hook_command` shell-quotes its path with `shlex.join`, so a naive `cmd.split()` breaks a
    quoted path apart at any space inside it -- exactly the kind of checkout Anush's own folder
    name (`HackMIT 2026`) produces, which is how he caught it in review on PR #44. Parse the
    command line the way a shell would.

    (Fixed independently on two branches; this keeps the stricter argv-tail assertion, which pins
    the subcommand as its own argv element rather than as a suffix of the whole string.)
    """
    cmd = _hook_command(event)
    argv = shlex.split(cmd)
    assert argv[0].startswith("/"), f"not absolute: {cmd}"
    assert os.path.exists(argv[0]), cmd
    assert argv[-2:] == ["_hook", event], cmd


@pytest.mark.parametrize("product", ["receipts", "custos-code"])
@pytest.mark.parametrize("fallback", [False, True])
def test_watch_replaces_old_hooks_and_preserves_unrelated_settings(
    tmp_path, monkeypatch, product, fallback,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    other = {"type": "command", "command": "echo receipts custos-code _hook"}
    groups = {}
    for event, action in zip(("PreToolUse", "PostToolUse", "Stop"), EVENTS, strict=True):
        argv = [f"/old install/bin/{product}", "_hook", action]
        if fallback:
            module = product.replace("-", "_")
            argv = ["/old install/bin/python", "-c", f"from {module}.cli import app; app()",
                    "_hook", action]
        old = {"type": "command", "command": shlex.join(argv)}
        groups[event] = [{"matcher": "Bash", "hooks": [old, other], "timeout": 19},
                         {"matcher": "", "hooks": [old]}]
    original = {"hooks": {**groups, "SessionStart": [{"hooks": [other]}]},
                "permissions": {"allow": ["Read"]}}
    settings.write_text(json.dumps(original))
    result = CliRunner().invoke(app, ["watch", "--install"])
    assert result.exit_code == 0, result.output
    assert json.loads(settings.with_suffix(".json.bak").read_text()) == original
    installed = json.loads(settings.read_text())
    assert installed["permissions"] == original["permissions"]
    assert installed["hooks"]["SessionStart"] == original["hooks"]["SessionStart"]
    for event, entries in hooks_snippet()["hooks"].items():
        assert installed["hooks"][event] == [
            {"matcher": "Bash", "hooks": [other], "timeout": 19}, *entries,
        ]
    result = CliRunner().invoke(app, ["watch", "--install"])
    assert result.exit_code == 0, result.output
    assert json.loads(settings.read_text()) == installed


@pytest.mark.parametrize("event", EVENTS)
def test_hook_fails_open_when_custos_code_cannot_run(event: str, tmp_path: pathlib.Path) -> None:
    """With nothing resolvable, a hook must exit 0 and say so -- never exit 2, which means block."""
    env = dict(os.environ)
    env["PATH"] = str(tmp_path)          # no custos-code, no uv, no python
    env["CUSTOS_CODE_BIN"] = str(tmp_path / "does-not-exist")
    r = _run(SCRIPTS[event], "{}", env)
    # The repo venv is still on disk, so resolution may legitimately succeed; what must never
    # happen is exit 2 (block) or a crash.
    assert r.returncode == 0, f"exit {r.returncode}: {r.stderr}"
    assert '"decision": "block"' not in r.stdout


@pytest.mark.parametrize("event", EVENTS)
def test_hook_never_exits_two_on_garbage_input(event: str) -> None:
    """Malformed payloads are an error in our parser, not evidence about the agent."""
    r = _run(SCRIPTS[event], "not json at all", dict(os.environ))
    assert r.returncode == 0, f"exit {r.returncode}: {r.stderr}"
    assert '"decision": "block"' not in r.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.skipif(not (HOOKS.parent / ".venv" / "bin").is_dir(),
                    reason="no repo venv (git worktree or bare clone); nothing to resolve to")
def test_runner_resolution_prefers_the_repo_venv() -> None:
    r = subprocess.run(
        [BASH, "-c", f'source "{HOOKS}/_run.sh" && custos_code_cmd'],
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip(), "resolved to nothing in a checkout that has a venv"
    # This asserted a property of the *environment*, not the code, and so failed in every git
    # worktree -- which is how PRs get reviewed here. Two reviewers reported it as a real failure
    # on 2026-09-19. A test that cannot hold in a worktree must skip there, not fail.


# --- the direct-binary path -------------------------------------------------------------
# `custos-code watch --install` writes a command that runs the console script with no shell wrapper,
# so nothing appends `|| true`. These exercise that path specifically: the .sh tests above pass
# even when main() raises, because the wrapper swallows it.

DIRECT = [shlex.split(_hook_command(e)) for e in EVENTS]


def _direct(argv: list[str], payload: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["CUSTOS_CODE_AUTO"] = "0"          # never block from a test
    env["HOME"] = env.get("TMPDIR", "/tmp")  # keep the real ~/.custos-code untouched
    return subprocess.run(argv, input=payload, capture_output=True, text=True, env=env, timeout=120)


@pytest.mark.parametrize("argv", DIRECT, ids=EVENTS)
@pytest.mark.parametrize("payload", ["", "not json", "{unclosed", "[]", "null", '"a string"',
                                     '{"session_id": "x"}'],
                         ids=["empty", "garbage", "truncated", "array", "null", "string", "minimal"])
def test_direct_invocation_never_crashes_or_blocks(argv: list[str], payload: str) -> None:
    r = _direct(argv, payload)
    assert r.returncode == 0, f"exit {r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
    assert "Traceback" not in r.stderr, r.stderr
    assert '"decision": "block"' not in r.stdout
    assert '"decision":"block"' not in r.stdout


def test_direct_stop_survives_a_payload_that_breaks_the_ledger() -> None:
    """A malformed tool_response must not stop the Stop hook from producing a receipt."""
    argv = shlex.split(_hook_command("stop"))
    r = _direct(argv, json.dumps({"session_id": "t", "last_assistant_message": "done",
                                  "cwd": "/nonexistent/path/that/does/not/exist"}))
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
