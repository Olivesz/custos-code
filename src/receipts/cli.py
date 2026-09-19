"""Typer entrypoint: check, watch, bench, eval, cost, and the internal `_hook` group.

`_hook` is what hooks/*.sh invoke; it is not a user-facing command (no one runs
`receipts _hook stop` by hand). Its subcommands read one hook payload from stdin and either
print a hook response JSON or exit silently. See docs/ADAPTERS.md §2 for the payload shapes.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import parsers, rerun
from .adapters import claude_code
from .models import EventKind

app = typer.Typer(help="Check a coding agent's final report against what it actually did.", no_args_is_help=True)
console = Console()
hook_app = typer.Typer(help="Internal: invoked by installed Claude Code hooks, not by hand.")
app.add_typer(hook_app, name="_hook")


@app.command()
def check(
    session: str | None = typer.Argument(None, help="Path to a session transcript (Claude Code JSONL)."),
    last: bool = typer.Option(False, "--last", help="Use the most recent Claude Code session."),
    events: bool = typer.Option(False, "--events", help="Also print the ledger."),
) -> None:
    """Print the receipt for one session. (v0: ledger + report; verdicts land with rules.py.)"""
    if last:
        session = claude_code.find_last_session()
    if not session:
        raise typer.BadParameter("give a session path or --last")
    sess, ledger, report = claude_code.parse(session)
    calls = sum(1 for e in ledger if e.kind == EventKind.CALL)
    flagged = sum(1 for e in ledger if e.flags.piped or e.flags.truncated or e.flags.error)
    console.print(f"[bold]receipts[/] session {sess.id[:8]}… · {sess.n_events} events · {calls} tool calls · "
                  f"{flagged} flagged · chain {sess.ledger_root_hash[:8]}… · cwd {sess.cwd}")
    if events:
        t = Table(show_header=True, header_style="dim")
        for col in ("#", "kind", "tool", "detail", "flags"):
            t.add_column(col)
        for e in ledger:
            detail = ""
            if e.kind == EventKind.CALL and e.input:
                detail = str(e.input.get("command") or e.input.get("file_path") or e.input.get("path") or "")[:90]
            elif e.output:
                detail = e.output.replace("\n", " ⏎ ")[:90]
            fl = " ".join(k for k, v in e.flags.model_dump().items() if v)
            t.add_row(str(e.seq), e.kind.value, e.tool or "", detail, fl)
        console.print(t)
    console.rule("final report")
    console.print(report or "[dim](no assistant text found)[/]")
    console.rule()
    console.print("[dim]verdicts: not implemented yet (rules.py, claims.py). Ledger and report are real.[/]")


@app.command()
def watch() -> None:
    """Install the PostToolUse and Stop hooks for live sessions."""
    raise typer.Exit(code=2)


@app.command()
def cost(session: str) -> None:
    """Tokens and dollars by tier for one session."""
    raise typer.Exit(code=2)


def _read_stdin_json() -> dict[str, object]:
    raw = sys.stdin.read()
    return json.loads(raw) if raw.strip() else {}


def _raw_ledger_path(session_id: str) -> Path:
    """Interim append-only store, one JSONL line per event, until ledger.LedgerStore (Oliver's
    SQLite + hash chain, currently NotImplementedError) exists. Not the Tier 0 integrity ledger;
    just enough for `_hook post-tool-use`/`stop` to have somewhere real to write."""
    d = Path.home() / ".receipts" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{session_id}.jsonl"


def _append_raw(session_id: str, record: dict[str, object]) -> None:
    with _raw_ledger_path(session_id).open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


@hook_app.command("pre")
def hook_pre() -> None:
    """PreToolUse: wrap known-runner Bash commands so Post can see the real resolved binary (E5)."""
    payload = _read_stdin_json()
    if payload.get("tool_name") != "Bash":
        return
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return
    wrapped = parsers.wrap_command_for_resolution(command)
    if wrapped is None:
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": {"command": wrapped},
        },
    }))


@hook_app.command("post-tool-use")
def hook_post_tool_use() -> None:
    """PostToolUse: strip the E5 resolution trailer (if any) before recording the event."""
    payload = _read_stdin_json()
    session_id = payload.get("session_id")
    if not isinstance(session_id, str):
        return
    tool_response = payload.get("tool_response")
    if isinstance(tool_response, str):
        output = tool_response
    elif isinstance(tool_response, dict):
        output = "\n".join(str(tool_response[k]) for k in ("stdout", "stderr") if tool_response.get(k))
    else:
        output = ""
    clean_output, resolved_bin, exit_code = parsers.strip_and_parse_trailer(output)
    _append_raw(session_id, {
        "kind": "call_result",
        "ts": datetime.now(UTC).isoformat(),
        "tool": payload.get("tool_name"),
        "cwd": payload.get("cwd"),
        "input": payload.get("tool_input"),
        "output": clean_output,
        "resolved_bin": resolved_bin,  # E5: feeds parsers.is_trusted_runner_path
        "exit_code": exit_code,
    })


@hook_app.command("stop")
def hook_stop() -> None:
    """Stop: Tier 0-2 rules block synchronously; Tier 3 spawns detached, never blocks (E4)."""
    payload = _read_stdin_json()
    session_id = payload.get("session_id")
    report = payload.get("last_assistant_message")
    if not isinstance(session_id, str) or not isinstance(report, str):
        return
    _append_raw(session_id, {"kind": "report", "ts": datetime.now(UTC).isoformat(), "text": report})
    # NEEDS-DECISION(oliver): claims.extract() and rules.check() are both still
    # NotImplementedError, so there is no way yet to know which claims this report makes or
    # which of them Tier 0-2 already settles vs. which are Tier-3-eligible and unsettled. Once
    # they exist, this blocks (raise typer.Exit(code=2) with the contradicted evidence) on their
    # verdicts only, and calls rerun.spawn_async(...) for each Tier-3-eligible unsettled claim
    # without waiting on it -- that spawn/poll/result-load path is implemented and independently
    # testable in rerun.py already (E4).
    raise NotImplementedError("claim extraction and rule checking are not wired yet")


@hook_app.command("rerun-worker")
def hook_rerun_worker(session_id: str, claim_id: str) -> None:
    """Detached entry point spawned by rerun.spawn_async; never invoked by a Claude Code hook."""
    rerun.run_worker(session_id, claim_id)
