"""Typer entrypoint: check, watch, bench, eval, cost, and the internal `_hook` group.

`_hook` is what hooks/*.sh invoke (and, for `rerun-worker`, what `rerun.spawn_async` invokes
directly); it is not a user-facing command. Its subcommands either read one hook payload from
stdin, or -- `rerun-worker` only -- take their identity as positional args since they have no
hook payload at all. See docs/ADAPTERS.md §2 for the payload shapes.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from . import adapters as adapters_mod
from . import claims as claims_mod
from . import judge as judge_mod
from . import report as report_mod
from . import review as review_mod
from . import verdicts as verdicts_mod
from .adapters import claude_code, codex, machine
from .models import Claim, EventKind, Verdict, VerdictRecord

MARK = {
    Verdict.CONFIRMED: ("✓", "green"),
    Verdict.CONTRADICTED: ("✗", "red"),
    Verdict.UNWITNESSED: ("?", "yellow"),
    Verdict.UNRECORDED: ("○", "bright_black"),
    Verdict.QUALIFIED: ("≈", "cyan"),
}

app = typer.Typer(
    help="Check a coding agent's final report against what it actually did.", no_args_is_help=True
)
console = Console()


@app.command()
def check(
    session: str | None = typer.Argument(
        None, help="Path to a session transcript, rollout, or bundle."
    ),
    last: bool = typer.Option(
        False, "--last", help="Use the most recent session of --agent (default Claude Code)."
    ),
    agent: str | None = typer.Option(
        None,
        "--agent",
        help="claude_code | codex | devin | copilot | machine | otel (default: detect).",
    ),
    events: bool = typer.Option(False, "--events", help="Also print the ledger."),
    repo: str | None = typer.Option(
        None, "--repo", help="Repo root for state checks (default: the session's cwd)."
    ),
    judge: bool = typer.Option(
        False, "--judge", help="Escalate semantic claims to the Tier 4 judge (needs an API key)."
    ),
    rules_only: bool = typer.Option(
        False, "--rules-only", help="Deterministic rules only; no model call."
    ),
) -> None:
    """Print the receipt for one session: every claim in the final report, with its verdict and evidence."""
    if last:
        session = codex.find_last_session() if agent == "codex" else claude_code.find_last_session()
    if not session:
        raise typer.BadParameter("give a session path or --last")
    sess, ledger, report = adapters_mod.parse(session, agent)
    calls = sum(1 for e in ledger if e.kind == EventKind.CALL)
    flagged = sum(1 for e in ledger if e.flags.piped or e.flags.truncated or e.flags.error)
    console.print(
        f"[bold]receipts[/] session {sess.id[:8]}… · {sess.n_events} events · {calls} tool calls · "
        f"{flagged} flagged · chain {sess.ledger_root_hash[:8]}… · cwd {sess.cwd}"
    )
    if events:
        t = Table(show_header=True, header_style="dim")
        for col in ("#", "kind", "tool", "detail", "flags"):
            t.add_column(col)
        for e in ledger:
            detail = ""
            if e.kind == EventKind.CALL and e.input:
                detail = str(
                    e.input.get("command") or e.input.get("file_path") or e.input.get("path") or ""
                )[:90]
            elif e.output:
                detail = e.output.replace("\n", " ⏎ ")[:90]
            fl = " ".join(k for k, v in e.flags.model_dump().items() if v)
            t.add_row(str(e.seq), e.kind.value, e.tool or "", detail, fl)
        console.print(t)
    console.rule("final report")
    console.print(report or "[dim](no assistant text found)[/]")
    console.rule("receipt")
    if not report:
        raise typer.Exit(code=0)
    cl = claims_mod.extract(report, sess.id)
    backend = None if rules_only else judge_mod.make_backend()
    if backend is not None and not judge:
        # default path: one call over the report and the annotated ledger (docs/GAPS.md, eval/arms)
        out = review_mod.review(report, ledger, sess.id, backend)
        cl, recs = out.claims, out.verdicts
        tail = f"one call ({out.input_tokens} in / {out.output_tokens} out)"
    else:
        if backend is None and not rules_only:
            console.print(
                "[yellow]no model backend: set OPENAI_API_KEY; falling back to rules only[/]"
            )
        cl = claims_mod.extract(report, sess.id)
        recs = verdicts_mod.run(cl, ledger, repo or sess.cwd, backend)
        tail = "rules only" if backend is None else f"rules + judge ({backend.usage.requests} req)"
    by_id = {c.id: c for c in cl}
    for r in recs:
        c = by_id[r.claim_id]
        mark, color = MARK[r.verdict]
        ev = " ".join(f"#{e}" for e in r.evidence) or "—"
        console.print(f"  [{color}]{mark} {r.verdict.value:<12}[/] {c.text[:88]}")
        console.print(
            f"      [dim]tier {r.tier} · {r.method} · {ev} · {r.rationale}{(' · ' + r.qualifier) if r.qualifier else ''}[/]"
        )
    s = verdicts_mod.summary(recs)
    parts = [f"{s[v.value]} {MARK[v][0]}" for v in Verdict if s[v.value]]
    console.print(
        f"[bold]receipts[/] {len(recs)} claims · {' · '.join(parts) if parts else 'no claims found'} · {tail}"
    )


HOOKS_SNIPPET = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": "receipts _hook pre", "timeout": 5}],
            }
        ],
        "PostToolUse": [
            {
                "matcher": "",
                "hooks": [
                    {"type": "command", "command": "receipts _hook post-tool-use", "timeout": 10}
                ],
            }
        ],
        "Stop": [
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": "receipts _hook stop", "timeout": 120}],
            }
        ],
    }
}


@app.command()
def watch(
    install: bool = typer.Option(
        False, "--install", help="Merge the hooks into ~/.claude/settings.json (backup kept)."
    ),
) -> None:
    """Show (or install) the Claude Code hooks that record every tool call and check each final report."""
    import json as _json
    import os as _os
    import shutil as _shutil

    if not install:
        console.print(_json.dumps(HOOKS_SNIPPET, indent=2))
        console.print(
            "[dim]Add to ~/.claude/settings.json (or .claude/settings.json in a repo), or run `receipts watch --install`.[/]"
        )
        return
    path = _os.path.expanduser("~/.claude/settings.json")
    data: dict[str, object] = {}
    if _os.path.exists(path):
        _shutil.copy(path, path + ".bak")
        with open(path, encoding="utf-8") as fh:
            data = _json.load(fh)
    hooks = data.setdefault("hooks", {})
    assert isinstance(hooks, dict)
    for ev, entries in HOOKS_SNIPPET["hooks"].items():
        existing = hooks.setdefault(ev, [])
        assert isinstance(existing, list)
        if not any("receipts _hook" in _json.dumps(e) for e in existing):
            existing.extend(entries)
    with open(path, "w", encoding="utf-8") as fh:
        _json.dump(data, fh, indent=2)
    console.print(f"installed into {path} (backup at {path}.bak)")


@app.command(name="_hook", hidden=True)
def _hook(
    event: str = typer.Argument(..., help="pre | post-tool-use | stop | rerun-worker"),
    session_id: str | None = typer.Argument(None, help="rerun-worker only: which session."),
    claim_id: str | None = typer.Argument(None, help="rerun-worker only: which claim."),
) -> None:
    """Internal: hook entrypoint. `pre`/`post-tool-use`/`stop` read the Claude Code payload on
    stdin. `rerun-worker` (E4) has no hook payload at all -- it's a detached subprocess
    `rerun.spawn_async` launches directly, so it takes its identity as positional args instead.
    """
    from . import hooks as _hooks

    raise typer.Exit(code=_hooks.main(event, session_id, claim_id))


@app.command(name="pr-comment")
def pr_comment(
    session: str = typer.Argument(
        ..., help="Session transcript or class-R bundle (Devin/Copilot) for the PR."
    ),
    agent: str | None = typer.Option(None, "--agent", help="Adapter to use (default: detect)."),
    out: str | None = typer.Option(
        None, "--out", help="Write the markdown here instead of stdout."
    ),
    repo: str | None = typer.Option(
        None, "--repo", help="Repo root for state checks (default: the session's cwd)."
    ),
    pr_url: str | None = typer.Option(None, "--pr-url", help="Link back to the PR in the footer."),
    receipt_url: str | None = typer.Option(None, "--receipt-url", help="Link to the full receipt."),
    rules_only: bool = typer.Option(
        False, "--rules-only", help="Deterministic rules only; no model call."
    ),
    fail_on_contradiction: bool = typer.Option(
        False,
        "--fail-on-contradiction",
        help="Exit 1 when a claim is contradicted (for a required check).",
    ),
) -> None:
    """Render the PR receipt as markdown (product sketch B); the Action posts what this prints."""
    sess, ledger, report = adapters_mod.parse(session, agent)
    cl: list[Claim] = []
    recs: list[VerdictRecord] = []
    if report:
        backend = None if rules_only else judge_mod.make_backend()
        cl = claims_mod.extract(report, sess.id, backend)
        recs = verdicts_mod.run(cl, ledger, repo or sess.cwd, backend)
    body = report_mod.pr_comment(sess, cl, recs, ledger, pr_url=pr_url, receipt_url=receipt_url)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(body)
        console.print(f"[dim]wrote {out}[/]")
    else:
        print(body)
    if fail_on_contradiction and any(r.verdict is Verdict.CONTRADICTED for r in recs):
        raise typer.Exit(code=1)


@app.command()
def record(
    shell: str = typer.Option("bash", "--shell", help="bash | zsh | sh: which rc snippet to emit."),
    install: bool = typer.Option(
        False, "--install", help="Append the snippet to the rc file (backup kept)."
    ),
) -> None:
    """Class-M recorder: log every shell command, exit status and cwd, with no harness at all."""
    import os as _os
    import shutil as _shutil

    snippet = machine.install_snippet(shell)
    if not install:
        print(snippet)
        console.print(
            f"[dim]Append to your rc file, or run `receipts record --shell {shell} --install`. "
            f"Log: {machine.default_log()}[/]"
        )
        return
    rc = _os.path.expanduser({"bash": "~/.bashrc", "sh": "~/.profile", "zsh": "~/.zshrc"}[shell])
    if _os.path.exists(rc):
        if "receipts recorder" in open(rc, encoding="utf-8").read():
            console.print(f"already installed in {rc}")
            return
        _shutil.copy(rc, rc + ".bak")
    _os.makedirs(machine.LOG_DIR, exist_ok=True)
    with open(rc, "a", encoding="utf-8") as fh:
        fh.write("\n" + snippet)
    console.print(f"installed into {rc} (backup at {rc}.bak) · log {machine.default_log()}")


@app.command(name="_record-line", hidden=True)
def _record_line() -> None:
    """Internal: the recorder shell hooks call this once per command to emit one wire line."""
    print(machine.record_line())


@app.command()
def cost(
    session: str | None = typer.Argument(
        None, help="Path to a session transcript (Claude Code JSONL)."
    ),
    last: bool = typer.Option(False, "--last", help="Use the most recent Claude Code session."),
    repo: str | None = typer.Option(
        None, "--repo", help="Repo root for state checks (default: the session's cwd)."
    ),
    judge: bool = typer.Option(
        False, "--judge", help="Escalate semantic claims to the Tier 4 judge (needs an API key)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Print JSON instead of a table."),
) -> None:
    """Tokens and dollars by tier for one session."""
    from . import cost as cost_mod

    if last:
        session = claude_code.find_last_session()
    if not session:
        raise typer.BadParameter("give a session path or --last")
    sess, ledger, report = claude_code.parse(session)
    cl = claims_mod.extract(report or "", sess.id)
    backend = judge_mod.make_backend() if judge else None
    if judge and backend is None:
        console.print(
            "[yellow]no judge backend: set OPENAI_API_KEY (or RECEIPTS_JUDGE_BACKEND=anthropic)[/]"
        )
    recs = verdicts_mod.run(cl, ledger, repo or sess.cwd, backend)
    usage = backend.usage if backend is not None else None
    c = cost_mod.compute(sess.id, recs, ledger, judge_usage=usage)
    if json_out:
        console.print_json(data=c.to_dict())
    else:
        console.print(cost_mod.render_table(c))
