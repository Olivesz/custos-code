"""Typer entrypoint: check, watch, bench, eval, cost, and the internal `_hook` group.

`_hook` is what hooks/*.sh invoke (and, for `rerun-worker`, what `rerun.spawn_async` invokes
directly); it is not a user-facing command. Its subcommands either read one hook payload from
stdin, or -- `rerun-worker` only -- take their identity as positional args since they have no
hook payload at all. See docs/ADAPTERS.md §2 for the payload shapes.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

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
    evidence: bool = typer.Option(
        False, "--evidence", help="Print the cited ledger lines under each claim."
    ),
    repo: str | None = typer.Option(
        None, "--repo", help="Repo root for state checks (default: the session's cwd)."
    ),
    rules_only: bool = typer.Option(
        False, "--rules-only", help="Deterministic rules only; no model call."
    ),
    ladder: bool = typer.Option(
        False, "--ladder", help="Use the superseded tiered pipeline instead of review."
    ),
    fmt: str = typer.Option("terminal", "--format", help="terminal | markdown | html"),
    out_path: str | None = typer.Option(
        None, "--out", help="Write the rendered receipt to a file."
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

    if fmt == "terminal":
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
                        e.input.get("command")
                        or e.input.get("file_path")
                        or e.input.get("path")
                        or ""
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

    backend = None if rules_only else judge_mod.make_backend()
    if backend is not None and not ladder:
        # Default: one call over the report and the annotated ledger. 86% vs the ladder's 70%
        # on construction-truth fixtures, McNemar p=0.00017 (eval/arms/RESULTS.md).
        reviewed = review_mod.review(report, ledger, sess.id, backend)
        claims, recs = reviewed.claims, reviewed.verdicts
        tail = f"one call · {reviewed.input_tokens} in / {reviewed.output_tokens} out"
    else:
        if backend is None and not rules_only:
            console.print(
                "[yellow]no model backend: set OPENAI_API_KEY; falling back to rules only[/]"
            )
        claims = claims_mod.extract(report, sess.id)
        recs = verdicts_mod.run(claims, ledger, repo or sess.cwd, backend)
        tail = (
            "rules only"
            if backend is None
            else f"rules + judge · {backend.usage.requests} requests"
        )

    if fmt == "markdown":
        text = report_mod.markdown(claims, recs, source=f"{sess.source} session {sess.id[:8]}")
    elif fmt == "html":
        text = report_mod.html_card(
            claims, recs, ledger, report=report, title=f"Receipt · {sess.id[:8]}"
        )
    else:
        report_mod.terminal(claims, recs, ledger, console, show_evidence=evidence)
        console.print(f"[dim]  {tail}[/]")
        text = None

    if out_path:
        body = (
            text
            if text is not None
            else report_mod.html_card(
                claims, recs, ledger, report=report, title=f"Receipt · {sess.id[:8]}"
            )
        )
        pathlib.Path(out_path).write_text(body, encoding="utf-8")
        console.print(f"[dim]wrote {out_path}[/]")
    elif text is not None:
        print(text)

    if any(r.verdict == Verdict.CONTRADICTED for r in recs):
        raise typer.Exit(code=1)


def _hook_command(event: str) -> str:
    """An absolutely-resolved command line for one hook event.

    Claude Code runs hooks through a shell that does not inherit this process's PATH, so a bare
    `receipts _hook stop` only works when receipts is installed globally. It is not when the repo
    is used from a checkout with a virtualenv -- the common case for this team, and the reason the
    hooks were silently not running on Oliver's laptop on 2026-09-19. Resolve now, at install time.

    Same resolution order as `rerun._worker_argv`, for the same reason and deliberately identical.
    """
    import shlex
    import shutil as _sh
    import sys as _sys

    args = ["_hook", event]
    script = _sh.which("receipts")
    if script:
        return shlex.join([script, *args])
    return shlex.join([_sys.executable, "-c", "from receipts.cli import app; app()", *args])


def hooks_snippet() -> dict[str, object]:
    """The settings.json fragment that installs the three hooks, with commands already resolved."""
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": _hook_command("pre"), "timeout": 5}],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": _hook_command("post-tool-use"),
                            "timeout": 10,
                        }
                    ],
                }
            ],
            "Stop": [
                {
                    "matcher": "",
                    "hooks": [
                        {"type": "command", "command": _hook_command("stop"), "timeout": 120}
                    ],
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
        console.print(_json.dumps(hooks_snippet(), indent=2))
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
    snippet = hooks_snippet()["hooks"]
    assert isinstance(snippet, dict)
    for ev, entries in snippet.items():
        existing = hooks.setdefault(ev, [])
        assert isinstance(existing, list)
        # Idempotence: match on the two stable tokens, not the literal command line. The command is
        # now an absolute path that varies per machine and per install layout, so the old
        # `"receipts _hook" in ...` test silently stopped matching and stacked duplicate hooks.
        if not any(("_hook" in (blob := _json.dumps(e)) and "receipts" in blob) for e in existing):
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
        pathlib.Path(out).write_text(body, encoding="utf-8")
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


@app.command()
def demo(
    scenario: str = typer.Option(
        "piped-runner", "--scenario", help="piped-runner | echoed-output | ghost-write | honest"
    ),
    out_path: str | None = typer.Option(None, "--out", help="Also write an HTML report card here."),
) -> None:
    """Run the whole loop on a known trap: the agent's claim, the evidence, the verdict, the nudge.

    Everything on screen is produced live from the fixture's own tool log. Nothing is pre-rendered,
    and the fixture is in the repo so anyone can read what the agent actually did.
    """
    import json as _json

    from . import feedback as feedback_mod

    picks = {
        "piped-runner": "trap_piped_0",
        "echoed-output": "trap_echo_0",
        "ghost-write": "trap_ghost_0",
        "honest": "ok_tests_0",
    }
    name = picks.get(scenario)
    if name is None:
        raise typer.BadParameter(f"scenario must be one of {', '.join(picks)}")
    fixture = (
        pathlib.Path(__file__).resolve().parents[2] / "eval" / "arms" / "fixtures" / f"{name}.jsonl"
    )
    if not fixture.exists():
        console.print("[yellow]fixtures missing — run `python eval/arms/generate.py` first[/]")
        raise typer.Exit(code=2)

    sess, ledger, report = claude_code.parse(str(fixture))
    task = next((e.output for e in ledger if e.kind == EventKind.USER), "")

    console.rule("[bold]1. what the developer asked for")
    console.print(f"  {task}")

    console.rule("[bold]2. what the agent actually did  (harness log, the model cannot write it)")
    for e in ledger:
        if e.kind == EventKind.CALL:
            v = str((e.input or {}).get("command") or (e.input or {}).get("file_path") or "")
            console.print(f"  [dim]#{e.seq}[/] [yellow]{e.tool}[/] {v[:100]}")
        elif e.kind == EventKind.RESULT and e.output:
            console.print(f"  [dim]#{e.seq}   → {e.output[:100].replace(chr(10), ' ⏎ ')}[/]")

    console.rule("[bold]3. what the agent said")
    console.print(f"  [italic]{report}[/]")

    # No key is a reason to show a quieter receipt, not to abandon the demo three stages in. The
    # deterministic rules catch this fixture's piped runner on their own, so the demo still runs
    # end to end; it just says less about why.
    backend = judge_mod.make_backend()
    if backend is not None:
        reviewed = review_mod.review(report or "", ledger, sess.id, backend)
        dclaims, drecs = reviewed.claims, reviewed.verdicts
        tail = f"one call · {reviewed.input_tokens} in / {reviewed.output_tokens} out"
    else:
        console.print("\n[yellow]no model backend — running the deterministic rules instead.[/]")
        console.print("[dim]  set OPENAI_API_KEY, or put it in ~/.receipts/env, for the full review.[/]")
        dclaims = claims_mod.extract(report or "", sess.id)
        drecs = verdicts_mod.run(dclaims, ledger, sess.cwd, None)
        tail = "rules only · 0 tokens"

    console.rule("[bold]4. the receipt")
    report_mod.terminal(dclaims, drecs, ledger, console, show_evidence=True)
    console.print(f"[dim]  {tail}[/]")

    open_pairs = [
        (c, r)
        for c, r in zip(dclaims, drecs, strict=True)
        if r.verdict.value in ("contradicted", "unrecorded")
    ]
    console.rule("[bold]5. what goes back to the agent")
    if open_pairs:
        reason = feedback_mod.build_block_reason(open_pairs, ledger, 1, 3)
        console.print(
            f"  [red]stop blocked[/] — {len(open_pairs)} claim(s) need work, nudge is a "
            f"template, [bold]0 LLM tokens[/]"
        )
        for line in reason.splitlines()[1:]:
            console.print(f"  [dim]{line[:160]}[/]")
        console.print(f"\n  [dim]hook returns:[/] {_json.dumps({'decision': 'block'})}")
    else:
        console.print(
            "  [green]nothing blocked[/] — every claim is confirmed or disclosed; the agent stops normally."
        )

    if out_path:
        pathlib.Path(out_path).write_text(
            report_mod.html_card(
                dclaims,
                drecs,
                ledger,
                report=report or "",
                title=f"Receipt · {scenario}",
            ),
            encoding="utf-8",
        )
        console.print(f"\n[dim]report card written to {out_path}[/]")


@dataclass
class _Mark:
    text: str
    verdict: str
    why: str
    evidence: list[int]


@dataclass
class _Scanned:
    path: str
    sid: str
    project: str
    marks: list[_Mark]


@app.command()
def scan(
    limit: int = typer.Option(25, "--limit", "-n", help="How many recent sessions to check."),
    workers: int = typer.Option(8, "--workers", help="Parallel model calls."),
    min_events: int = typer.Option(5, "--min-events", help="Skip sessions with fewer tool calls."),
    all_marks: bool = typer.Option(False, "--all", help="Show every verdict, not just contradictions."),
    out_path: str | None = typer.Option(None, "--out", help="Write the findings as JSON."),
) -> None:
    """Check your own recent sessions and report what an agent told you that the log contradicts.

    This is the honest demo. A staged trap only fires when the agent takes the bait, and a careful
    agent does not -- so a trap that catches nothing looks like a broken product when it is in fact
    behaving correctly. Real history contains the failures that actually occur (a sample reported
    as a total, a remembered test count, a "verified working" that skipped the command that
    failed), and the evidence is already on the machine.
    """
    import concurrent.futures as _cf

    from .adapters.claude_code import find_sessions

    backend = judge_mod.make_backend()
    if backend is None:
        console.print("[yellow]scan needs a model backend: set OPENAI_API_KEY or ~/.receipts/env[/]")
        raise typer.Exit(code=2)

    paths = find_sessions()
    console.print(f"[dim]{len(paths)} sessions on disk; checking the {limit} most recent[/]\n")

    def one(path: str) -> _Scanned | None:
        try:
            sess, ledger, rep = claude_code.parse(path)
        except Exception:
            return None
        if not rep or len(ledger) < min_events:
            return None
        try:
            r = review_mod.review(rep, ledger, sess.id, backend)
        except Exception:
            return None
        proj = pathlib.Path(path).parent.name.replace("-Users-oliverzhang-", "")
        return _Scanned(path, pathlib.Path(path).stem[:8], proj,
                        [_Mark(c.text, v.verdict.value, v.rationale, list(v.evidence))
                         for c, v in zip(r.claims, r.verdicts, strict=False)])

    done: list[_Scanned] = []
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, p) for p in paths[: limit * 3]]
        for f in _cf.as_completed(futs):
            got = f.result()
            if got is None:
                continue
            done.append(got)
            if len(done) >= limit:
                break

    shown = {"contradicted"} if not all_marks else {v.value for v in Verdict}
    tally: dict[str, int] = {v.value: 0 for v in Verdict}
    for s in done:
        for m in s.marks:
            tally[m.verdict] += 1

    hits = 0
    for s in done:
        picked = [m for m in s.marks if m.verdict in shown]
        if not picked:
            continue
        hits += 1
        console.print(f"[bold]{s.sid}[/] [dim]{s.project}[/]")
        for m in picked:
            glyph, colour = MARK[Verdict(m.verdict)]
            cites = " ".join(f"#{e}" for e in m.evidence) or "—"
            console.print(f"  [{colour}]{glyph} {m.verdict}[/] {m.text[:150]}")
            console.print(f"    [dim]{cites} · {m.why[:210]}[/]")
        console.print()

    total = sum(tally.values()) or 1
    answered = tally["confirmed"] + tally["contradicted"] + tally["qualified"]
    bad = sum(1 for s in done if any(m.verdict == "contradicted" for m in s.marks))
    console.rule()
    console.print(f"  {len(done)} sessions · {total} claims · coverage {answered}/{total} = {answered / total:.0%}")
    console.print(f"  [red]{tally['contradicted']} contradicted[/] across "
                  f"[bold]{bad} of {len(done)} sessions ({bad / max(len(done), 1):.0%})[/]")
    console.print(f"  [dim]confirmed {tally['confirmed']} · qualified {tally['qualified']} · "
                  f"unwitnessed {tally['unwitnessed']} · unrecorded {tally['unrecorded']}[/]")
    if not hits and not all_marks:
        console.print("  [green]nothing contradicted in this window[/] — rerun with --all to see every mark")

    if out_path:
        import json as _j
        payload = {"tally": tally,
                   "sessions": [{"sid": s.sid, "project": s.project, "path": s.path,
                                 "marks": [vars(m) for m in s.marks]} for s in done]}
        pathlib.Path(out_path).write_text(_j.dumps(payload, indent=1), encoding="utf-8")
        console.print(f"  [dim]wrote {out_path}[/]")
