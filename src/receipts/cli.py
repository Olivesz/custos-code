"""Typer entrypoint: check, watch, bench, eval, cost."""
from __future__ import annotations

import typer

app = typer.Typer(help="Check a coding agent's final report against what it actually did.")


@app.command()
def check(session: str = typer.Argument(None), last: bool = typer.Option(False, "--last")) -> None:
    """Print the receipt for one session (or the most recent Claude Code session with --last)."""
    raise typer.Exit(code=2)  # NEEDS-DECISION(oliver): wire adapters -> claims -> verdicts -> report


@app.command()
def watch() -> None:
    """Install the PostToolUse and Stop hooks for live sessions."""
    raise typer.Exit(code=2)


@app.command()
def cost(session: str) -> None:
    """Tokens and dollars by tier for one session."""
    raise typer.Exit(code=2)
