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


def test_watch_replaces_hooks_that_carry_env_assignments(tmp_path, monkeypatch) -> None:
    """The literal commands found in ~/.claude/settings.json on 2026-09-20, verbatim.

    Every hook this project has ever installed on a real machine carries `VAR=value` prefixes, so
    this is the only shape that has ever mattered -- and it was the one shape the replace logic did
    not recognise. `shlex.split` puts the assignment in args[0], the binary check reads args[0],
    and the match fails. The consequence was not cosmetic: these three commands point at a
    pre-rename console script that raises ModuleNotFoundError on every tool call, and the migration
    written to retire them skipped straight past.

    Asserts the count as well as the content, because the failure mode is a stacked duplicate --
    the stale hook keeps erroring and a working one is appended beside it.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    stale = "RECEIPTS_ONLY_IN=/Users/oliverzhang/cart-service RECEIPTS_AUTO=1 /Users/oliverzhang/Projects/receipts/.venv/bin/receipts _hook {}"
    settings.write_text(json.dumps({"hooks": {
        event: [{"matcher": "", "hooks": [{"type": "command", "command": stale.format(action)}]}]
        for event, action in zip(("PreToolUse", "PostToolUse", "Stop"), EVENTS, strict=True)
    }}))
    assert CliRunner().invoke(app, ["watch", "--install"]).exit_code == 0
    installed = json.loads(settings.read_text())["hooks"]
    for event, entries in hooks_snippet()["hooks"].items():
        assert installed[event] == list(entries), f"{event} was not replaced"
    assert not any("receipts _hook" in json.dumps(v) for v in installed.values()), \
        "a pre-rename hook survived the migration and will keep erroring on every tool call"


@pytest.mark.parametrize("event", EVENTS)
def test_the_command_we_install_actually_runs(event: str, tmp_path: pathlib.Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Execute the literal string from settings.json, through a shell, exactly as Claude Code does.

    This is the test that was missing. `test_direct_invocation_never_crashes_or_blocks` runs
    `_hook_command()` resolved fresh at test time, so it passes even when the command recorded in
    settings.json is stale and broken -- which it was on the owner's machine for hours after the
    `receipts` -> `custos_code` rename, raising ModuleNotFoundError on every tool call. Nothing
    surfaced it: PostToolUse failures are non-blocking by design.

    So: install for real, read the command back out of the file, and run it through `bash -c` with
    the env assignments intact. Anything that breaks the install-then-execute path fails here.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    result = CliRunner().invoke(app, ["watch", "--install", "--only-in", str(tmp_path / "proj")])
    assert result.exit_code == 0, result.output
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    ev = {"pre": "PreToolUse", "post-tool-use": "PostToolUse", "stop": "Stop"}[event]
    command = settings["hooks"][ev][0]["hooks"][0]["command"]
    assert "CUSTOS_CODE_ONLY_IN=" in command, "the fence was dropped on install"

    payload = json.dumps({"session_id": "e2e", "cwd": str(tmp_path / "proj"),
                          "tool_name": "Bash", "tool_input": {"command": "echo hi"},
                          "tool_response": {"stdout": "hi"},
                          "last_assistant_message": "I ran echo."})
    env = {**os.environ, "HOME": str(tmp_path), "PATH": ""}
    proc = subprocess.run([BASH, "-c", command], input=payload, capture_output=True,
                          text=True, env=env, timeout=120)
    assert proc.returncode == 0, f"installed hook failed: rc={proc.returncode} {proc.stderr[:400]}"
    assert "Traceback" not in proc.stderr, proc.stderr[:400]
    # Exit 0 and a clean stderr prove nothing on their own: `hooks.main()` catches everything and
    # exits 0 by design, so a handler that raises on its first line still looks like this. An
    # earlier version of this test asserted only those two things and passed with the entire
    # recorder gutted -- the precise failure it was written to prevent. Assert the side effect.
    assert "hook failed" not in proc.stderr, f"the hook caught and swallowed an error: {proc.stderr[:400]}"
    if event == "post-tool-use":
        live = pathlib.Path(tmp_path) / ".custos-code" / "live" / "e2e.jsonl"
        assert live.exists(), "the hook ran and exited 0 but recorded nothing"
        rows = [json.loads(ln) for ln in live.read_text().splitlines() if ln.strip()]
        kinds = [r.get("kind") for r in rows]
        assert "call" in kinds and "result" in kinds, f"ledger is missing events: {kinds}"


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
