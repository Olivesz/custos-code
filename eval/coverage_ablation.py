#!/usr/bin/env python3
"""G5 ("Coverage is 7%"): three arms over the same corpus, so the number GAPS.md cites has an
answer instead of sitting unmeasured.

Rewritten after review (Oliver, 2026-09-19) of the first version, which got three things wrong
badly enough that its numbers shouldn't be trusted:

1. **It measured a pipeline nobody ships.** `cli.py` runs `review.review()` by default whenever a
   judge backend exists and `--ladder` isn't passed -- that one call over the report and the
   annotated ledger *is* the product. The old script's "rules only" / "+ re-run" / "+ judge" arms
   were all built on `claims.extract_regex` + `verdicts.run`, the superseded tiered pipeline that
   only ships behind `--ladder`. `review.review()` is now the primary arm here; `verdicts.run`
   still appears, but as an explicit "rules + judge escalation" arm measured *alongside* it, not
   instead of it -- that comparison is its own useful answer (does escalating the deterministic
   rules to a judge earn its place, holding extraction constant), it just isn't the shipping
   number.
2. **The "+ judge" arm was a hardcoded skip, not code.** `TierResult("+ judge", ..., note="skipped:
   ...")` never constructed a backend or called anything; setting an API key changed nothing. Both
   judge-dependent arms below are now real calls (`judge_mod.make_backend()`, same as `cli.py`);
   they report `skipped` only when that returns `None`.
3. **No intervals, and the finding doesn't survive one.** Wilson intervals for 9/21 (rules) and
   10/21 (+ old re-run arm) overlap almost completely -- the entire "coverage moved" conclusion
   the first run's GAPS.md insert drew was noise at that sample size. Every coverage number below
   carries a 95% Wilson interval (`eval/arms/evaluate.wilson`, the same helper GAPS.md already
   asks for) and the trial count is always shown, never a bare percentage.

**The re-run arm is gone, not fixed.** `rerun_tests` replays against `HEAD+working-tree` -- the
repo *as it is right now* -- with no reconciliation between a session's timestamp and the commit
it actually ran against. A session recorded against a tree that built cleanly, re-run today
against a `main` that (until #55) could not even collect its own test suite, would have its true
`run_tests` claim re-classified `contradicted` for a reason that has nothing to do with whether
the agent lied -- the exact failure mode `3433813` fixed elsewhere in the ledger path, reproduced
here in eval code instead. Commit-pinning (worktree-checkout the session's actual head, not
today's) would fix this properly; nothing here or in `rerun.py` does that yet, so this script
doesn't claim to measure re-run at all rather than measure it wrong.

**The corpus is scoped explicitly now, not by `os.path.isdir(cwd)`.** That filter was a
re-run-specific requirement (needs a real, still-existing directory to replay in) silently applied
to every arm, which is why the first run undercounted: sessions whose checkout had since moved or
been deleted were dropped even though their report and ledger are still perfectly readable. It also
said nothing about *whose* sessions they were -- `~/.claude/projects/*/*.jsonl` spans every project
on the machine, including ones that have nothing to do with this repo. Default scope is this
repo's own root (or `CUSTOS_CODE_ONLY_IN`, the same env var `hooks.py` already uses to keep an agent
under test from reading its own audit config, if set); pass `--roots` for a different, explicit
choice. Nothing here executes anything in any of them -- only `claude_code.parse` on an already
recorded transcript -- so this scoping is about honesty and consent (whose sessions end up in a
committed results doc), not sandboxing a subprocess.

**Still not the team's gold corpus.** That corpus (93 sessions, 121 claims) lives on whoever ran
the original G5 measurement; this environment has none of it. Treat every number this script
prints as a small local sample it is honest about the size of, not a replication of the cited 7%.

Usage: uv run python eval/coverage_ablation.py [--roots /path/one,/path/two] [--backend openai]
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from arms.evaluate import wilson  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from custos_code import claims as claims_mod  # noqa: E402
from custos_code import judge as judge_mod  # noqa: E402
from custos_code import review as review_mod  # noqa: E402
from custos_code.adapters import claude_code  # noqa: E402
from custos_code.models import LedgerEvent, Verdict, VerdictRecord  # noqa: E402
from custos_code.verdicts import run as verdicts_run  # noqa: E402

console = Console()
REPO_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))


@dataclass
class Session:
    id: str
    cwd: str
    ledger: list[LedgerEvent]
    report: str


@dataclass
class TierResult:
    name: str
    total: int = 0
    covered: int = 0  # not unwitnessed
    accusations: int = 0  # contradicted
    note: str = ""
    skipped: bool = False


def _default_roots() -> list[str]:
    """`CUSTOS_CODE_ONLY_IN` if set (same env var `hooks._out_of_scope` reads), else this repo."""
    env = os.environ.get("CUSTOS_CODE_ONLY_IN", "").strip()
    if env:
        return [os.path.realpath(os.path.expanduser(p)) for p in env.split(os.pathsep) if p.strip()]
    return [REPO_ROOT]


def _in_roots(cwd: str, roots: list[str]) -> bool:
    try:
        cwd_r = os.path.realpath(cwd)
    except OSError:
        return False
    for root in roots:
        try:
            if os.path.commonpath([root, cwd_r]) == root:
                return True
        except ValueError:
            continue  # different drives on Windows; never a match
    return False


def local_sessions(roots: list[str]) -> list[Session]:
    """Local transcripts with a report, scoped to `roots` -- see the module docstring for why this
    is no longer `os.path.isdir(sess.cwd)`."""
    out: list[Session] = []
    for path in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
        try:
            sess, ledger, report = claude_code.parse(path)
        except Exception:  # noqa: BLE001 -- a malformed transcript should not kill the whole run
            continue
        if not (report and report.strip() and sess.cwd and _in_roots(sess.cwd, roots)):
            continue
        out.append(Session(sess.id, sess.cwd, ledger, report))
    return out


def _score(recs: list[VerdictRecord]) -> tuple[int, int]:
    covered = sum(1 for r in recs if r.verdict != Verdict.UNWITNESSED)
    accused = sum(1 for r in recs if r.verdict == Verdict.CONTRADICTED)
    return covered, accused


def tier_rules_only(sessions: list[Session]) -> TierResult:
    """Baseline: the no-key, no-network fallback. `claims.extract_regex` + `verdicts.run` with no
    backend -- deterministic rules only, exactly what a machine with no API key gets today."""
    tr = TierResult("rules only")
    for s in sessions:
        cl = claims_mod.extract_regex(s.report, s.id)
        recs = verdicts_run(cl, s.ledger, s.cwd)
        covered, accused = _score(recs)
        tr.total += len(recs)
        tr.covered += covered
        tr.accusations += accused
    return tr


def tier_rules_plus_judge(sessions: list[Session], backend: object | None) -> TierResult:
    """Same extraction as the baseline, with the ladder's judge escalation actually invoked for
    claims still `unwitnessed` after rules -- isolates what the judge buys *over rules alone*,
    holding extraction constant. This is the superseded `--ladder` pipeline's own escalation path,
    not the shipping default; measured here because G5 asked whether the judge earns its place."""
    tr = TierResult("rules + judge escalation")
    if backend is None:
        tr.skipped = True
        tr.note = "skipped: no OPENAI_API_KEY/ANTHROPIC_API_KEY"
        return tr
    for s in sessions:
        cl = claims_mod.extract_regex(s.report, s.id)
        recs = verdicts_run(cl, s.ledger, s.cwd, backend)
        covered, accused = _score(recs)
        tr.total += len(recs)
        tr.covered += covered
        tr.accusations += accused
    return tr


def tier_review(sessions: list[Session], backend: object | None) -> TierResult:
    """`review.review()`: the actual shipping default (cli.py, no `--ladder`, backend present).
    Its own LLM extraction, not `extract_regex` -- a different claim set than the other two arms,
    on purpose, because that's what running `custos-code check` for real gets you."""
    tr = TierResult("review (shipping default)")
    if backend is None:
        tr.skipped = True
        tr.note = "skipped: no OPENAI_API_KEY/ANTHROPIC_API_KEY"
        return tr
    for s in sessions:
        reviewed = review_mod.review(s.report, s.ledger, s.id, backend)
        covered, accused = _score(reviewed.verdicts)
        tr.total += len(reviewed.verdicts)
        tr.covered += covered
        tr.accusations += accused
    return tr


def render(tiers: list[TierResult]) -> Table:
    t = Table(show_header=True, header_style="dim",
             title="G5 coverage ablation (local sessions, not the gold corpus)")
    for col in ("tier", "coverage (95% Wilson CI)", "accusations", "note"):
        t.add_column(col)
    for tier in tiers:
        if tier.skipped or not tier.total:
            pct, acc = "—", "—"
        else:
            lo, hi = wilson(tier.covered, tier.total)
            pct = f"{tier.covered}/{tier.total} ({tier.covered / tier.total:.0%}, [{lo:.0%}, {hi:.0%}])"
            acc = str(tier.accusations)
        t.add_row(tier.name, pct, acc, tier.note)
    return t


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--roots", default=None,
                    help=f"Comma-separated dirs to scope the corpus to (default: "
                         f"$CUSTOS_CODE_ONLY_IN if set, else this repo: {REPO_ROOT}).")
    ap.add_argument("--backend", default=None, help="openai | anthropic (default: judge.make_backend()'s own).")
    args = ap.parse_args(argv)

    roots = (
        [os.path.realpath(os.path.expanduser(p)) for p in args.roots.split(",") if p.strip()]
        if args.roots
        else _default_roots()
    )
    sessions = local_sessions(roots)
    if not sessions:
        console.print(f"[red]no local sessions with a report found under {', '.join(roots)}[/]")
        return 1

    backend = judge_mod.make_backend(args.backend)
    console.print(
        f"[dim]{len(sessions)} local session(s) scoped to {', '.join(roots)} -- NOT the team's "
        f"gold corpus; see module docstring[/]"
    )
    if backend is None:
        console.print("[yellow]no model backend: set OPENAI_API_KEY or ANTHROPIC_API_KEY -- "
                      "judge-dependent arms will report skipped[/]")

    rules_tier = tier_rules_only(sessions)
    judge_tier = tier_rules_plus_judge(sessions, backend)
    review_tier = tier_review(sessions, backend)

    console.print(render([rules_tier, judge_tier, review_tier]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
