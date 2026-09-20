"""Current single-pass review vs the coordinator/worker/critic pipeline, on known truth.

Two things this is built to answer, which no number in this repo currently answers:

  1. Is the proposed pipeline better, at equal budget? Accuracy alone is not the comparison --
     the proposed arm spends up to two model calls per claim, so it must earn that.
  2. Is either of them REPRODUCIBLE? A measured 22.8% verdict flip between identical runs means a
     single-run accuracy figure is not evidence. Every arm runs k times and reports pass^k: the
     share of claims it gets right EVERY time. A checker that is right on average and wrong a
     quarter of the time is not a gate.

Usage:  .venv/bin/python eval/compare.py [--n 30] [--k 2]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import proposed as proposed_mod  # noqa: E402

from custos_code import judge as judge_mod  # noqa: E402
from custos_code import review as review_mod  # noqa: E402
from custos_code.adapters import claude_code  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / "eval" / "arms" / "fixtures"
TRUTH = json.loads((ROOT / "eval" / "arms" / "truth.json").read_text())


def _match(text: str, want: str) -> bool:
    a, b = text.lower(), want.lower()
    return a[:22] in b or b[:22] in a


def _score(claims, recs, wants) -> tuple[int, int, int, list[str]]:
    """Right, total, false accusations, per-claim keys that were right."""
    by_id = {c.id: c.text for c in claims}
    right = 0
    false_acc = 0
    ok_keys: list[str] = []
    for want in wants:
        hit = next((r for r in recs if _match(by_id.get(r.claim_id, ""), want["claim"])), None)
        got = hit.verdict.value if hit else "missing"
        if got == want["verdict"]:
            right += 1
            ok_keys.append(want["claim"][:40])
        if got == "contradicted" and want["verdict"] != "contradicted":
            false_acc += 1
    return right, len(wants), false_acc, ok_keys


def run_arm(arm: str, fixtures: list[pathlib.Path], backend) -> dict:
    right = total = false_acc = calls = 0
    t0 = time.monotonic()
    per_claim: dict[str, bool] = {}
    for fx in fixtures:
        wants = TRUTH.get(fx.stem) or []
        if not wants:
            continue
        _s, ledger, report = claude_code.parse(str(fx))
        if not report:
            continue
        try:
            if arm == "current":
                out = review_mod.review(report, ledger, fx.stem, backend, repo_root=str(ROOT))
                claims, recs = out.claims, out.verdicts
                calls += out.requests
            else:
                claims, recs = proposed_mod.check(report, ledger, fx.stem, backend, str(ROOT))
                calls += proposed_mod.MAX_MODEL_CALLS  # upper bound; refined below
        except Exception as exc:  # a crash is a result, not an excuse to drop the row
            print(f"    {arm} {fx.stem}: {type(exc).__name__}", file=sys.stderr)
            total += len(wants)
            for w in wants:
                per_claim[f"{fx.stem}::{w['claim'][:40]}"] = False
            continue
        r, n, fa, ok = _score(claims, recs, wants)
        right, total, false_acc = right + r, total + n, false_acc + fa
        for w in wants:
            per_claim[f"{fx.stem}::{w['claim'][:40]}"] = w["claim"][:40] in ok
    return {"right": right, "total": total, "false_acc": false_acc,
            "secs": round(time.monotonic() - t0, 1), "calls": calls, "per_claim": per_claim}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--k", type=int, default=2)
    args = ap.parse_args()

    backend = judge_mod.make_backend()
    if backend is None:
        print("no model backend; this comparison needs one")
        return 2

    names = sorted(TRUTH)
    random.Random(20260920).shuffle(names)
    fixtures = [FIX / f"{n}.jsonl" for n in names if (FIX / f"{n}.jsonl").exists()][: args.n]
    print(f"{len(fixtures)} fixtures x {args.k} runs x 2 arms, model {backend.judge_model}\n")

    results: dict[str, list[dict]] = {"current": [], "proposed": []}
    for arm in ("current", "proposed"):
        for i in range(args.k):
            res = run_arm(arm, fixtures, backend)
            results[arm].append(res)
            acc = res["right"] / max(res["total"], 1) * 100
            print(f"  {arm:9} run {i + 1}: {res['right']:3}/{res['total']:3} = {acc:5.1f}%  "
                  f"false-acc {res['false_acc']:2}  {res['secs']:6.1f}s")

    print(f"\n{'arm':10} {'mean acc':>9} {'pass^k':>8} {'false-acc':>10} {'secs':>7} {'flip%':>7}")
    print("-" * 56)
    for arm, runs in results.items():
        accs = [r["right"] / max(r["total"], 1) for r in runs]
        keys = set(runs[0]["per_claim"])
        always = sum(1 for k in keys if all(r["per_claim"].get(k) for r in runs))
        flips = sum(1 for k in keys
                    if len({r["per_claim"].get(k) for r in runs}) > 1)
        print(f"{arm:10} {sum(accs) / len(accs) * 100:8.1f}% {always / max(len(keys), 1) * 100:7.1f}% "
              f"{sum(r['false_acc'] for r in runs) / len(runs):9.1f} "
              f"{sum(r['secs'] for r in runs) / len(runs):6.1f} {flips / max(len(keys), 1) * 100:6.1f}%")
    print("\npass^k = share of claims the arm got right on EVERY run. flip% = share that changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
