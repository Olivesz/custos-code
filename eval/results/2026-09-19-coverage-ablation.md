# G5 coverage ablation: rules only vs + re-run vs + judge

Run: `uv run python eval/coverage_ablation.py`.

**Not the team's gold corpus.** `docs/GAPS.md` G5 measured 7% coverage (8/121 claims) on 93
real local sessions from whoever's machine ran it originally; this session has none of that
corpus (checked -- 0/10 of the SWE-chat-half gold ids exist locally either, per issue #27's own
mining tool). What ran here instead is this machine's own local Claude Code transcripts: 9
sessions, 21 regex-extracted claims, all of them this project's own dogfooding sessions. Smaller,
narrower (heavy on test/build/edit claims for a Python tooling repo, not the general mix the
93-session corpus had), and not a replication of the cited 7% -- a different sample answering the
same structural question the ablation is for.

| tier | coverage | accusations | note |
|---|---|---|---|
| rules only | 9/21 (43%) | 2 | |
| + re-run | 10/21 (48%) | 3 | |
| + judge | — | — | skipped: no `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` in this environment |

**The re-run tier is not dead weight, on this sample.** It moved one claim from `unwitnessed` to
settled, and that claim settled as a genuine new `contradicted` (a real test command, replayed in
an isolated worktree, gave positive evidence the rules-only pass couldn't have had -- rules-only
has no path from "unwitnessed" to "contradicted" for a `run_tests`/`build` claim with no recorded
result at all). That is exactly the "earns its place" case G5 asks the ablation to distinguish
from dead weight, though n=1 additional claim is far too small to generalize from.

**The re-run tier itself did not exist as an integration before this script.** Per
`docs/OPEN_QUESTIONS.md` E4, `rules.py`'s `rule_run_tests` has no Tier-3 escalation path today --
`verdicts.run` never calls `rerun.rerun_tests` for any claim. `eval/coverage_ablation.py`'s
`_reclassify_from_rerun` is a standalone measurement harness built to answer this one question
(same decision shape as `rules._outcome_of`: parse the re-run's real output with `parsers.parse`,
contradict on 0-collected/failures/non-zero exit, confirm on a clean parsed pass, leave alone on
anything unparseable) -- it is not a change to `rules.py`/`verdicts.py`, and wiring a real Tier-3
escalation path into the ladder itself is still open (same OPEN_QUESTIONS.md E4 row).

**The judge tier is unmeasured, not zero.** No key, no run, no number -- reported as "skipped" in
both the table and this file rather than guessed at or silently omitted.

**What would make this a real answer instead of a demonstration:** the same script run against
the actual 93-session gold corpus, by whoever has it, ideally with a judge backend so all three
tiers report real numbers side by side.
