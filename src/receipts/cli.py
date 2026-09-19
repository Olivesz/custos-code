"""Typer entrypoint: check, watch, bench, eval, cost."""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from .adapters import claude_code
from .models import EventKind

app = typer.Typer(help="Check a coding agent's final report against what it actually did.", no_args_is_help=True)
console = Console()


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
