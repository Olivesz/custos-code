# Gaps and how we close them rigorously

Written after attacking our own measurements (`eval/results/2026-09-19-extraction.md`,
`docs/METHODS.md`). Each gap states the finding, why the current evidence is insufficient, and the
specific experiment that closes it. Ordered by how badly a rigorous reviewer would hurt us.

---

## G1 — Accusation precision is undefined (n = 0)

**Finding.** After the path fix, the engine makes **zero** `contradicted` verdicts across 93 real
local sessions and 121 extracted claims. Our headline safety claim ("we do not falsely accuse") has
no supporting data. Not a good rate: no rate at all.

**Why more real sessions will not fix it.** Precision is a property of the positive class. You
cannot estimate it from a sample in which you never predict positive. Collecting another 500 honest
sessions would still give n = 0 accusations and tell us nothing. This is the classic rare-positive
estimation problem, and the standard answer is to *construct* the positive class rather than wait
for it.

**The experiment that closes it.** Two corpora with ground truth known by construction:

- **Trap sessions (positive class).** A repo fixture plus a task whose honest completion is
  impossible, run through a real agent: a `pytest | head` that exits 0 on "collected 0 items", a
  broken runner, a deleted failing test, a ghost write. An oracle that does not read the report
  knows the truth. Any accusation the checker makes here is a **true positive by construction**.
- **Negative controls (honest class).** Sessions where the agent genuinely did the work, verified by
  the same oracle. Any accusation here is a **false positive by construction**.

From those two we get precision, recall and F1 on the accusation class with **zero human
labelling**, because the labels come from the environment rather than a judgement.

**Reporting standard.** Wilson intervals, not point estimates, and the number of trials stated. With
`k` traps × `m` models × `n` runs the sample size is explicit and the intervals will be honest.

**Watch for.** Trap sessions are synthetic, so a trap-only precision figure is optimistic about the
wild. Report it as "precision on adversarially-constructed cases", and keep the real-corpus
abstention rate beside it as the complementary number.

---

## G2 — The metamorphic suite is 60% passable by a do-nothing checker

**Finding.** A stub whose `run()` returns `unwitnessed` for every claim passes **9 of 15** relation
tests. Most of our relations are *safety* properties ("must not remain confirmed"), which a constant
abstainer satisfies for free. Only 6 are *liveness* properties ("must become contradicted", "must
become unrecorded").

**Why this matters.** A suite that a trivial system passes does not evidence correctness. Stated
formally: our relations are necessary conditions, and we have been presenting them as if they were
close to sufficient.

**The experiment that closes it.** Two moves.

1. **Add liveness relations** so that abstention is punished: for every seed where evidence is
   present and unambiguous, the verdict must be a specific answer, not merely "not confirmed".
2. **Measure suite strength directly with a mutation score.** Mechanically mutate the checker
   (invert a comparison, drop a guard, widen a regex, return the wrong verdict constant), run the
   suite, and report the fraction of mutants killed. That is the standard answer to "is your test
   suite any good", and unlike a pass count it cannot be satisfied by a do-nothing implementation.
   Target: publish the mutation score, whatever it is.

**Closed (2026-09-19).** `eval/mutation/run.py` already implemented move 2; running it against
the suite as it stood found **68% (15/22)**, with the 7 survivors printed by name -- each one a
mutant the suite did not notice, not a guess. One more mutant (`claims.py`, "absolute paths are
truncated again") reported "pattern did not apply" instead of a real result: its match pattern
had stopped matching after a ruff-format pass reflowed the surrounding lines, so it had silently
been testing nothing.

Move 1: added five new liveness relations (`tests/metamorphic/test_relations.py` MR10-14) and
two regression tests (`tests/unit/test_claims.py`: did-not-touch polarity, and the absolute-path
truncation case its own code comment names but never had a test for), each targeting one of the
7 survivors and grounded in the actual `rules.py`/`verdicts.py`/`claims.py` code path it
exercises, not written to the mutant's diff. Also re-anchored the broken mutant's pattern on the
current source (verified with `re.subn(count=1) == 1` against the live file) so it tests
something again.

Re-running the mutation score afterward: **96% (22/23)**. The one remaining survivor,
`accusable()`'s "missing repo state" guard, appears to be dead code given the current call
sites -- `rule_edit`/`rule_create`/`rule_delete` all gate on `RepoState.exists()`/`.changed()`,
which already return `None` (not `False`) whenever `state.root` is invalid, so `accusable()`'s
own root check is never reached with a `False`-shaped input in practice. Worth a second look from
Oliver (rules.py is his file) to confirm before deleting the guard or leaving it as defence in
depth -- left open rather than assumed.

---

## G3 — The labeller we scored against is unreliable

**Finding.** Three runs of the same strict prompt over the same 204 sentences found **73, 62 and 67**
claims. Pairwise Cohen's κ was 0.72–0.81, but only **61%** of the claims it ever names appear in all
three runs.

**Why this matters.** Every extraction number we have (regex recall 0.14, classifier recall 0.60)
was scored against a single noisy draw from that instrument. It also caps what any system can score:
you cannot demonstrate accuracy beyond the reliability of your reference.

**The experiment that closes it.**

1. **Ensemble the reference.** Use majority-of-3 as the label and report the residual instability.
   Variance of a majority vote is lower; quantify by how much rather than assuming.
2. **Calibrate against humans on a small sample.** 30 sentences labelled by two people, compared
   against the machine majority. That yields machine-vs-human κ, which is the number that says
   whether the machine reference is usable at all. Thirty rows is ten minutes, not forty-five.
3. **Report the ceiling explicitly.** If human-vs-human κ on the same 30 rows is 0.7, then 0.7 is
   the ceiling for any system and a system near it is at human level, not failing.

This is where the scarce human effort belongs: calibrating the instrument, not labelling the corpus.

---

## G4 — The capture–recapture estimate is too unstable to cite

**Finding.** Chapman's bias-corrected estimator gives N̂ = 101 with a 95% CI of **[67, 135]**, 67% of
the estimate. Classifier recall comes out 0.62 with CI **[0.47, 0.94]**. The overlap is nAB = 9, and
the estimator's variance blows up at small overlap.

**Also an assumption problem.** Lincoln–Petersen and Chapman both assume the two detectors fail
independently. A regex and a language model both key on surface lexical cues, so they will miss the
same terse and unusual claims. Positive dependence inflates the overlap, which deflates N̂, which
**overstates recall**. Our recall figures are therefore upper bounds, and we do not know by how much.

**The experiment that closes it.** On the trap corpus the set of true claims is known by
construction, so recall is measured directly and no estimator is needed. Keep capture–recapture only
as an order-of-magnitude sanity check on the wild corpus, always with the CI and the dependence
caveat attached, or drop it from the pitch.

---

## G5 — Coverage is 7%

**Finding.** Of 121 claims extracted across the real corpus, the checker answers 8 and abstains on
113. A tool that is safe because it almost never speaks is not yet a product.

**The experiment that closes it.** An **ablation**: run the same corpus with tiers progressively
enabled (rules only; + re-run; + judge) and report coverage and accusation count at each step. That
answers two questions at once: how much coverage each tier buys, and whether any tier is dead
weight. If the judge moves coverage from 7% to something material without adding accusations, it has
earned its place; if it does not, we should say so and cut it.

Part of the low coverage is upstream: the regex extracts unanswerable junk, which inflates the
denominator. Measure coverage against classifier-extracted claims too, and report both.

**Partially run (2026-09-19).** `eval/coverage_ablation.py` implements the ablation and ran it --
not against the 93-session gold corpus, which this environment doesn't have (see the script's own
module docstring), but against this machine's own 9 local dogfooding sessions (21 claims):
rules-only 9/21 (43%) coverage, 2 accusations; + re-run 10/21 (48%), 3 accusations; + judge
skipped (no API key). Full writeup: `eval/results/2026-09-19-coverage-ablation.md`. The re-run
tier moved one claim from `unwitnessed` to a genuine new `contradicted` on this sample -- not
dead weight here, though n=1 is far too small to generalize. Also built, not just measured: the
Tier-3 escalation path itself didn't exist before this script (E4/E5's own admission that
`rule_run_tests` has no path from rules to a re-run); `_reclassify_from_rerun` is a standalone
harness for measurement, not a change to `rules.py`/`verdicts.py` -- wiring re-run into the ladder
for real is still open. The judge tier and the classifier-extracted-claims comparison are both
still unmeasured, and the real 93-session numbers still need whoever has that corpus to run this
script against it.

---

## G6 — The corpus is one person's sessions

**Finding.** All 93–120 sessions come from a single user and skew toward docs, planning and repo
hygiene rather than test-and-build coding. The claim distribution, and therefore every rate we
report, may not transfer.

**The experiment that closes it.** SWE-chat (issue #27): 6,000 real sessions across Claude Code,
Codex, Cursor and Copilot, ODC-BY, auto-gated. Sample stratified by agent and task type. Until then,
every rate in the deck carries the sampling caveat in the same sentence, not in a footnote.

---

## Priority under a deadline

| | Gap | Closes | Effort |
|---|---|---|---|
| 1 | G1 traps + negative controls | the undefined safety number, with construction-known labels | high value, moderate effort |
| 2 | G2 mutation score + liveness relations | "your suite proves nothing" | low effort, high credibility |
| 3 | G5 ablation | "why five tiers", and the 7% coverage problem | low effort, one run |
| 4 | G3 30 human labels | whether any extraction number is usable | 10 minutes of human time |
| 5 | G6 SWE-chat | external validity | blocked on account access |
| 6 | G4 | drop it or caveat it; superseded by G1 | free |

The through-line: **every remaining number should come from an environment that knows the truth, or
carry an interval and a stated assumption.** Nothing asserted.
