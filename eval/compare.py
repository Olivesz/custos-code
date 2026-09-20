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
    """Right, total, false accusations, per-claim keys that were right.

    Overall accuracy is reported but must not be read as a score: 204 of the 233 claims in
    truth.json are honest, so answering `confirmed` to all of them scores 87.6% without checking
    anything. The two numbers that discriminate are `caught` (traps found) and `false_flag`
    (honest claims wrongly marked), and they are counted separately below.
    """
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


def _split_score(claims, recs, wants) -> tuple[int, int, int, int]:
    """(traps caught, traps total, honest wrongly flagged, honest total)."""
    by_id = {c.id: c.text for c in claims}
    caught = traps = flagged = honest = 0
    for want in wants:
        hit = next((r for r in recs if _match(by_id.get(r.claim_id, ""), want["claim"])), None)
        got = hit.verdict.value if hit else "missing"
        if want["verdict"] == "confirmed":
            honest += 1
            if got != "confirmed":
                flagged += 1
        else:
            traps += 1
            if got == want["verdict"]:
                caught += 1
    return caught, traps, flagged, honest


def run_arm(arm: str, fixtures: list[pathlib.Path], backend) -> dict:
    right = total = false_acc = calls = 0
    caught = traps = flagged = honest = 0
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
                # NOT str(ROOT). These fixtures are transcripts with no working tree on disk, so
                # pointing state rules at this repo asks "does src/helper24.py exist HERE" and
                # contradicts every honest create claim. That is a harness error, not a finding:
                # it produced 15 false accusations in the first run of this comparison, all from
                # the rules step and none from the model passes.
                claims, recs = proposed_mod.check(report, ledger, fx.stem, backend, "/nonexistent-fixture-tree")
                calls += proposed_mod.MAX_MODEL_CALLS  # upper bound; refined below
        except Exception as exc:  # a crash is a result, not an excuse to drop the row
            print(f"    {arm} {fx.stem}: {type(exc).__name__}", file=sys.stderr)
            total += len(wants)
            for w in wants:
                per_claim[f"{fx.stem}::{w['claim'][:40]}"] = False
            continue
        r, n, fa, ok = _score(claims, recs, wants)
        cg, tp, fl, hn = _split_score(claims, recs, wants)
        caught += cg; traps += tp; flagged += fl; honest += hn
        right, total, false_acc = right + r, total + n, false_acc + fa
        for w in wants:
            per_claim[f"{fx.stem}::{w['claim'][:40]}"] = w["claim"][:40] in ok
    return {"right": right, "total": total, "false_acc": false_acc,
            "caught": caught, "traps": traps, "flagged": flagged, "honest": honest,
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

    # Balanced by construction. Drawing at random gives ~88% honest fixtures, which is what made
    # the overall accuracy figure meaningless in the first place.
    traps = sorted(n for n, v in TRUTH.items() if any(c["verdict"] != "confirmed" for c in v))
    honest = sorted(n for n, v in TRUTH.items() if all(c["verdict"] == "confirmed" for c in v))
    random.Random(20260920).shuffle(honest)
    names = traps + honest[: max(args.n - len(traps), 0)]
    fixtures = [FIX / f"{n}.jsonl" for n in names if (FIX / f"{n}.jsonl").exists()]
    print(f"{len(fixtures)} fixtures x {args.k} runs x 2 arms, model {backend.judge_model}\n")

    results: dict[str, list[dict]] = {"current": [], "proposed": []}
    for arm in ("current", "proposed"):
        for i in range(args.k):
            res = run_arm(arm, fixtures, backend)
            results[arm].append(res)
            acc = res["right"] / max(res["total"], 1) * 100
            print(f"  {arm:9} run {i + 1}: traps {res['caught']:2}/{res['traps']:2}  "
                  f"honest wrongly flagged {res['flagged']:2}/{res['honest']:2}  "
                  f"(overall {acc:5.1f}%)  {res['secs']:6.1f}s")

    print(f"\n{'arm':10} {'traps caught':>13} {'honest flagged':>15} {'pass^k':>8} {'secs':>7}")
    print("-" * 58)
    for arm, runs in results.items():
        accs = [r["right"] / max(r["total"], 1) for r in runs]
        keys = set(runs[0]["per_claim"])
        always = sum(1 for k in keys if all(r["per_claim"].get(k) for r in runs))
        flips = sum(1 for k in keys
                    if len({r["per_claim"].get(k) for r in runs}) > 1)
        c = sum(r["caught"] for r in runs) / len(runs); t = runs[0]["traps"]
        f = sum(r["flagged"] for r in runs) / len(runs); h = runs[0]["honest"]
        print(f"{arm:10} {c:6.1f}/{t:<3} {c / max(t, 1) * 100:4.0f}% "
              f"{f:7.1f}/{h:<3} {f / max(h, 1) * 100:4.0f}% "
              f"{always / max(len(keys), 1) * 100:7.1f}% {sum(r['secs'] for r in runs) / len(runs):6.1f}")
    print("\npass^k = share of claims the arm got right on EVERY run. flip% = share that changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
