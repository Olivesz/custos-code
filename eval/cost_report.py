#!/usr/bin/env python3
"""The cost chart (Token Company track, issue #17): on the same sessions, three arms.

  (a) frontier      -- judge every claim, on the full unwindowed ledger, no ladder at all. The
                        cost this whole product argues against paying.
  (b) ladder         -- today's default: rules settle tiers 0-2 for free, only the residue reaches
                        the judge, batched into one windowed request (verdicts.run + judge.window_for_all).
  (c) ladder+bear-2  -- (b), with the judge's window pre-shrunk by compress.py's bear-2 pass before
                        those tokens would be sent.

For each arm, on each session: dollars (cost.compute, priced from config.toml's `[prices]` table),
tokens, and Cohen's kappa against the gold verdict labels. AGENTS.md invariant 8 says ship the
regex baseline beside the judge; this chart holds the ladder to the same rule against itself --
a dollar figure with no kappa next to it is not a comparison, it's a number.

Two honesty notes, stated here so they can't get lost in a slide:

1. **Kappa needs gold verdict labels that do not exist yet.** `eval/gold/claims_to_label.csv` has
   extraction labels only; `eval/gold/reconciled.csv` (verdict labels, three passes reconciled,
   per eval/gold/LABELLING_GUIDE.md) has not been produced (docs/OPEN_QUESTIONS.md B4). Every
   kappa column below reads "pending" until that file exists. Reporting a fabricated or borrowed
   kappa here would be exactly the failure mode this whole product exists to catch.

2. **Arm (c)'s dollar figure is a projection, not an independently re-judged measurement.**
   `compress.Compressor.compress_window` really calls bear-2 and really measures tokens saved on
   the ladder's actual windowed residue -- that part is not simulated. But turning the compressed
   text into new verdicts would mean substituting it into judge.py's prompt assembly, which is
   deliberately left unwired for Oliver to decide (docs/OPEN_QUESTIONS.md E11; see compress.py's
   module docstring). So arm (c) reuses arm (b)'s verdicts and judge token counts, and reports the
   dollar figure *as if* the measured token savings had applied -- labelled "projected" in the
   output -- rather than asserting accuracy was preserved without having measured it. "The
   accuracy-preserved claim is the creative part; a saving that costs kappa is not a saving" is
   the brief's own line, and it applies here first: this script refuses to claim it until E11 is
   wired and arm (c) can be judged for real.

Needs a judge backend to produce real numbers (OPENAI_API_KEY or ANTHROPIC_API_KEY); without one,
every judge-dependent arm is skipped with a note rather than reporting zeroes as if they were real.

Sessions are read from Claude Code's local transcript store (`~/.claude/projects/**/<id>.jsonl`);
transcripts themselves are never committed (eval/gold/sessions.txt has ids only), so this script
only reports on whatever gold sessions still exist on the machine running it, and says which ids
it could not find rather than silently shrinking the sample.

Usage: uv run python eval/cost_report.py [--sessions eval/gold/sessions.txt] [--gold eval/gold/reconciled.csv]
       [--out eval/cost_report.json]

Owner: Anush.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from custos_code import claims as claims_mod  # noqa: E402
from custos_code import compress as compress_mod  # noqa: E402
from custos_code import cost as cost_mod  # noqa: E402
from custos_code import judge as judge_mod  # noqa: E402
from custos_code import rules as rules_mod  # noqa: E402
from custos_code import verdicts as verdicts_mod  # noqa: E402
from custos_code.adapters import claude_code  # noqa: E402
from custos_code.models import Claim, LedgerEvent  # noqa: E402
from custos_code.verdicts import _escalates  # noqa: E402

console = Console()


# ---------- locating sessions and gold labels ----------


def find_session(session_id: str, projects_dir: str | None = None) -> str | None:
    root = projects_dir or os.path.expanduser("~/.claude/projects")
    hits = glob.glob(os.path.join(root, "*", f"{session_id}.jsonl"))
    return hits[0] if hits else None


def load_session_ids(sessions_file: str) -> list[str]:
    """`eval/gold/sessions.txt`: `local\t<id>\tcalls=N\tregex_claims=N`, `#`-comments, blank lines."""
    ids: list[str] = []
    with open(sessions_file, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                ids.append(parts[1])
    return ids


def load_gold_verdicts(path: str) -> dict[str, str] | None:
    """claim_id -> reconciled verdict label. `None` (not `{}`) when the file does not exist yet --
    callers must treat "no file" and "file with no matching claims" differently: the first means
    "pending", the second means "measured, and this session had no overlap with the gold set"."""
    if not os.path.exists(path):
        return None
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            cid = row.get("claim_id") or row.get("row_id")
            label = row.get("label")
            if cid and label and label != "not_a_claim":
                out[cid] = label
    return out


def cohen_kappa(pred: list[str], gold: list[str]) -> float | None:
    """Plain two-rater Cohen's kappa. No numpy/sklearn dependency for a handful of labels per
    session; this is not a hot path."""
    n = len(pred)
    if n == 0 or n != len(gold):
        return None
    labels = sorted(set(pred) | set(gold))
    idx = {label: i for i, label in enumerate(labels)}
    k = len(labels)
    confusion = [[0] * k for _ in range(k)]
    for p, g in zip(pred, gold, strict=True):
        confusion[idx[p]][idx[g]] += 1
    po = sum(confusion[i][i] for i in range(k)) / n
    row_marg = [sum(confusion[i]) / n for i in range(k)]
    col_marg = [sum(confusion[i][j] for i in range(k)) / n for j in range(k)]
    pe = sum(row_marg[i] * col_marg[i] for i in range(k))
    if pe >= 1.0:
        return 1.0
    return (po - pe) / (1 - pe)


def _kappa_for(recs: list[Any], gold: dict[str, str] | None) -> float | None:
    if gold is None:
        return None  # pending: no gold file at all
    paired = [(r.verdict.value, gold[r.claim_id]) for r in recs if r.claim_id in gold]
    if not paired:
        return None
    pred, truth = zip(*paired, strict=True)
    return cohen_kappa(list(pred), list(truth))


def _pending_claims(claims_: list[Claim], ledger: list[LedgerEvent], repo_root: str | None) -> list[Claim]:
    """The same escalation filter verdicts.run applies internally, exposed here so arm (c) can
    build the identical judge window arm (b) would have used, without re-running the ladder twice."""
    out = []
    for c in claims_:
        found = rules_mod.check(c, ledger, repo_root)
        if _escalates(found):
            out.append(c)
    return out


# ---------- the three arms ----------


@dataclass
class ArmResult:
    name: str
    session_id: str
    model: str = ""
    dollars: float = 0.0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    kappa: float | None = None
    note: str = ""


def arm_frontier(session_id: str, claims_: list[Claim], ledger: list[LedgerEvent],
                  backend: judge_mod.Backend | None, gold: dict[str, str] | None) -> ArmResult:
    """(a) judge every claim, on the full ledger, no rules, no windowing."""
    if backend is None:
        return ArmResult("frontier", session_id, note="no judge backend")
    if not claims_:
        return ArmResult("frontier", session_id, note="no claims")
    recs = backend.judge(claims_, ledger)
    c = cost_mod.compute(session_id, recs, ledger, judge_usage=backend.usage)
    return ArmResult("frontier", session_id, backend.usage.model, c.judge_dollars, c.judge_input_tokens,
                     c.judge_cached_input_tokens, c.judge_output_tokens, c.judge_requests,
                     _kappa_for(recs, gold))


def arm_ladder(session_id: str, claims_: list[Claim], ledger: list[LedgerEvent], repo_root: str | None,
               backend: judge_mod.Backend | None, gold: dict[str, str] | None) -> ArmResult:
    """(b) rules first; only the residue reaches the judge, windowed, one request per session."""
    if backend is None:
        return ArmResult("ladder", session_id, note="no judge backend")
    recs = verdicts_mod.run(claims_, ledger, repo_root, backend)
    c = cost_mod.compute(session_id, recs, ledger, judge_usage=backend.usage)
    return ArmResult("ladder", session_id, backend.usage.model, c.judge_dollars, c.judge_input_tokens,
                     c.judge_cached_input_tokens, c.judge_output_tokens, c.judge_requests,
                     _kappa_for(recs, gold))


def arm_ladder_compressed(session_id: str, claims_: list[Claim], ledger: list[LedgerEvent],
                          repo_root: str | None, compressor: compress_mod.Compressor | None,
                          ladder: ArmResult, prices: cost_mod.PriceTable) -> ArmResult:
    """(c) real bear-2 call on the ladder's actual windowed residue, measuring real tokens saved.
    The dollar figure is *projected*: arm (b)'s real judge token counts with the measured savings
    subtracted, priced at arm (b)'s own judge model -- not an independent re-judging of compressed
    text (that needs judge.py wiring left for Oliver, E11). Verdicts and kappa are arm (b)'s own;
    this arm never claims a kappa of its own, because it never produced independent verdicts."""
    if ladder.note == "no judge backend":
        return ArmResult("ladder+bear-2", session_id, note="no judge backend")
    if compressor is None:
        return ArmResult("ladder+bear-2", session_id, note="compressor off (config or key missing)")
    pending = _pending_claims(claims_, ledger, repo_root)
    if not pending or ladder.requests == 0:
        return ArmResult("ladder+bear-2", session_id, note="no residue reached the judge")
    win = judge_mod.window_for_all(ledger, pending)
    compressor.compress_window(win)  # real call; usage accumulates on the compressor
    saved = compressor.usage.tokens_saved
    projected_input = max(0, ladder.input_tokens - saved)
    projected_usage = judge_mod.Usage(requests=ladder.requests, input_tokens=projected_input,
                                      cached_input_tokens=ladder.cached_input_tokens,
                                      output_tokens=ladder.output_tokens, model=ladder.model)
    c = cost_mod.compute(session_id, [], [], judge_usage=projected_usage, prices=prices)
    return ArmResult(
        "ladder+bear-2", session_id, ladder.model, c.judge_dollars,
        projected_input, ladder.cached_input_tokens, ladder.output_tokens, ladder.requests, kappa=None,
        note=f"projected: {saved} tokens saved by bear-2 on the real windowed residue; "
             "verdicts and kappa inherited from arm (b), not independently re-judged (E11)",
    )


# ---------- report ----------


def run_session(session_path: str, backend_factory: Any, compressor_factory: Any,
                gold: dict[str, str] | None, prices: cost_mod.PriceTable) -> list[ArmResult]:
    """Each arm gets its own fresh backend instance (a fresh `Usage` counter), so their token and
    dollar figures don't bleed into each other -- three real per-arm requests per session, not
    one shared meter three ways."""
    sess, ledger, report = claude_code.parse(session_path)
    cl = claims_mod.extract(report or "", sess.id)

    a = arm_frontier(sess.id, cl, ledger, backend_factory(), gold)
    b = arm_ladder(sess.id, cl, ledger, sess.cwd, backend_factory(), gold)
    c = arm_ladder_compressed(sess.id, cl, ledger, sess.cwd, compressor_factory(), b, prices)
    return [a, b, c]


def _fmt_kappa(k: float | None) -> str:
    return "pending" if k is None else f"{k:.2f}"


def render(results: dict[str, list[ArmResult]]) -> Table:
    t = Table(show_header=True, header_style="dim", title="cost_report: frontier vs ladder vs ladder+bear-2")
    for col in ("session", "arm", "$", "tokens (in/cached/out)", "kappa", "note"):
        t.add_column(col)
    for session_id, arms in results.items():
        for arm in arms:
            t.add_row(session_id[:8] + "…", arm.name, f"{arm.dollars:.4f}",
                      f"{arm.input_tokens}/{arm.cached_input_tokens}/{arm.output_tokens}",
                      _fmt_kappa(arm.kappa), arm.note)
    return t


def to_json(results: dict[str, list[ArmResult]]) -> dict[str, Any]:
    return {
        sid: [
            {"arm": a.name, "model": a.model, "dollars": round(a.dollars, 6), "input_tokens": a.input_tokens,
             "cached_input_tokens": a.cached_input_tokens, "output_tokens": a.output_tokens,
             "requests": a.requests, "kappa": a.kappa, "note": a.note}
            for a in arms
        ]
        for sid, arms in results.items()
    }


def main(argv: list[str] | None = None) -> int:
    here = os.path.dirname(__file__)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sessions", default=os.path.join(here, "gold", "sessions.txt"))
    ap.add_argument("--gold", default=os.path.join(here, "gold", "reconciled.csv"))
    ap.add_argument("--out", default=os.path.join(here, "cost_report.json"))
    args = ap.parse_args(argv)

    ids = load_session_ids(args.sessions)
    gold = load_gold_verdicts(args.gold)
    if gold is None:
        console.print("[yellow]no eval/gold/reconciled.csv yet -- every kappa column below is 'pending'[/]")
    prices = cost_mod.load_prices()

    results: dict[str, list[ArmResult]] = {}
    missing: list[str] = []
    for sid in ids:
        path = find_session(sid)
        if path is None:
            missing.append(sid)
            continue
        results[sid] = run_session(path, judge_mod.make_backend, compress_mod.make_compressor, gold, prices)

    if missing:
        console.print(f"[yellow]{len(missing)} gold session id(s) not found on this machine, skipped:[/] "
                      + ", ".join(m[:8] + "…" for m in missing))
    if not results:
        console.print("[red]no sessions to report on[/]")
        return 1

    console.print(render(results))
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(to_json(results), fh, indent=2)
    console.print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
