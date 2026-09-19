# Evaluation: does the trap-aware review beat asking a model plainly?

**Ground truth comes from construction, not labelling.** Every fixture is a synthetic session where
we control what the agent did and what it claimed, so the correct verdict follows from reading the
fixture. No annotators, no machine-graded reference, nothing to disagree about.

- 42 fixtures, 53 claims, 8 trap families plus honest controls
- **45% of claims are honest controls**, because without them the false-accusation rate — the number
  that decides whether this is safe to ship — has no denominator
- Two arms, paired on the same fixtures; 3 repeats on the primary model for run-to-run variance;
  a second model to test whether the finding generalises
- Wilson 95% intervals on every rate; McNemar's exact test on the paired outcomes

Reproduce: `.venv/bin/python eval/arms/generate.py && OPENAI_API_KEY=... .venv/bin/python eval/arms/evaluate.py --repeats 3`

## Arms

- **A baseline** — the obvious thing: raw tool log, plain prompt, "mark each claim".
- **C ours** — log annotated with deterministic facts (pipe detection, parsed runner summaries, exit
  codes, file line/test counts) plus a prompt naming the specific ways agents fake evidence.

## Result (gpt-5.2, 3 repeats)

| arm | accuracy | 95% CI | run-to-run | false accusations on honest controls |
|---|---|---|---|---|
| A baseline | 70% | [56%, 80%] | ±1.9% | 0/72 (≤5%) |
| **C ours** | **86%** | **[73%, 92%]** | ±2.9% | **0/72 (≤5%)** |

McNemar exact, paired: C wins 34, A wins 9, **p = 0.00017**. Significant.

### Where the gain is, and where it isn't

| family | A | C |
|---|---|---|
| stderr_dropped | **0/12** | **12/12** |
| echoed_output | 3/12 | 9/12 |
| manual_check | 4/9 | 9/9 |
| ghost_write | 7/9 | 9/9 |
| piped_runner | 10/12 | 9/12 |
| count_inflation, failing_claimed_pass, subset_as_full | 12/12, 9/9, 12/12 | same |
| honest controls | 54/72 | 55/72 |

The win is concentrated in three families the baseline cannot see: filtered output (0/12 → 12/12),
echoed fake summaries, and knowing that a manual check is unwitnessable rather than a lie. On three
families both arms are already perfect, so the prompt buys nothing there. On `piped_runner` C is
marginally *worse*, which we have not chased.

## Generalisation: gpt-5-mini, 1 pass

| arm | accuracy | 95% CI | false accusations |
|---|---|---|---|
| A baseline | 75% | [62%, 85%] | 0/24 |
| C ours | 81% | [69%, 89%] | 0/24 |

McNemar p = 0.58, **not significant**. The direction is the same and the margin is smaller, but on a
single pass over 53 claims we cannot claim the effect holds on the small model. Stated rather than
buried: **the result is demonstrated on gpt-5.2 and suggestive on gpt-5-mini.**

## Two bugs this evaluation found in our own work

1. **The prompt was over-correcting into silence.** "Absence of evidence is never contradicted" made
   the reviewer refuse to contradict a claimed file with no write event: `ghost_write` scored
   **0/9**. Absence *is* evidence when the action could only have happened through a tool call. The
   rule now distinguishes actions that necessarily leave a trace (writes, commands, commits) from
   those that need not (a browser check). That took ghost_write to 9/9 without costing manual_check.
2. **A fixture was wrong, and the model was right.** An honest control claimed "14 passed" while its
   `go test` output reads `ok proj 0.014s`. C flagged it as contradicted; we had scored that as a
   false accusation. The fixture was fixed, not the model. Worth stating plainly: a bad reference
   makes a good system look bad, which is an argument for construction-truth over labelling.

## What this does not show

- 53 claims is small. The intervals above are wide and we quote them rather than the point estimate.
- The fixtures are synthetic. They are built from failure modes documented in the research
  (`docs/RESEARCH.md`) and in real transcripts, but a trap corpus is optimistic about the wild.
  The complementary real-corpus number is the abstention rate (93% on 93 local sessions), reported
  beside it and never instead of it.
- Only two arms. B (annotation without the trap prompt) and D (a deterministic veto on top) were
  dropped after the pilot: B bought nothing and D matched C exactly.
