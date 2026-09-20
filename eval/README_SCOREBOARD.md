# Scoreboard — where the product actually is

Three harnesses. The first needs nothing but the repo; the other two need a model key.

## `scoreboard.py` — the baseline. Run this first.

```
.venv/bin/python eval/scoreboard.py
```

Drives the **real** hooks (`on_pre_tool_use` → `on_post_tool_use` → `on_stop`) against scenarios
whose truth is known by construction: a real git repo, real commands, real output. No API key, no
network, no model — so it is **deterministic**, and a number from it means the same thing twice.

Each scenario declares either the verdict that must appear, or a verdict that must NOT (confirming
a lie is the one outcome with no defence). Half the set is honest work that must pass untouched,
because a checker that flags good work is as broken as one that misses a lie.

Current: **10/10**. Verified non-vacuous — reverting the stdout fix gives 9/10, disabling the
count check gives 8/10.

## `compare.py` — current pipeline vs the proposed one

```
.venv/bin/python eval/compare.py --n 30 --k 2
```

Scores both arms on `eval/arms/fixtures` against `truth.json`, `k` times each, and reports
**pass^k**: the share of claims an arm gets right on *every* run, not on average.

That metric is the point. A repeat-run measurement on 18 real sessions found **22.8% of verdicts
flip** between identical runs. A single-run accuracy figure for anything model-driven in this repo
is not evidence, and the Wilson intervals published alongside those figures do not capture it.

## `proposed.py` — the coordinator/worker/critic pipeline

Not wired into the product. Rules run first so a claim the log settles never costs a model call;
the worker returns its assumptions and gaps rather than a bare verdict; the critic re-reads the
evidence without seeing the worker's reasoning; disagreement or an unresolved gap lands as
explicitly unverified. Hard call and time limits live in code, not in a prompt.

## Known-bad, do not quote

`eval/arms/README.md` reports 100% (9/9) from a superseded pilot. `RESULTS.md` reports 86% over 53
claims. `docs/SCOPE.md` reports 87% and 0/408. `truth.json` holds 122 fixtures / 233 claims, which
matches none of them. The "vs the ladder's 70%" comparison in `cli.py` and `hooks.py` never
measured the ladder — arm A is a plain-prompt LLM over a raw log.
