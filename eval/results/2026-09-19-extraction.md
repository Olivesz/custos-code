# Extraction measurement, 19 Sep 2026

Question: how good is the regex baseline actually, and can a model do better?

## Setup
- Corpus: the 10 local gold sessions (`eval/gold/sessions.txt`, seed 20260919), 204 sentences.
- Labels: an independent pass with a strict "is this a checkable claim about work the agent did"
  prompt, `gpt-5.2`, judging each sentence with no knowledge of what any extractor said. 63 of
  204 sentences labelled as claims.
- Scored: recall, precision, F1 of each extractor against those labels.

## Results

| Extractor | recall | precision | F1 | cost per session |
|---|---|---|---|---|
| regex baseline | 0.14 | 0.60 | 0.23 | 0 |
| LLM, whole-report extraction, loose prompt | — | — | — | over-extracted 5x (79 claims vs 15) |
| LLM, whole-report extraction, tightened prompt | 0.32 | 0.87 | 0.47 | ~1.7k in / 0.4k out |
| **LLM, per-sentence classification, gpt-5-mini** | **0.95** | 0.57 | 0.71 | ~0.7k in / 2.0k out |
| **LLM, per-sentence classification, gpt-5.2** | 0.68 | **0.83** | **0.75** | ~0.7k in / 0.3k out |

## What this changed
1. **The regex cannot be the primary extractor.** It recovers one claim in seven. Its misses are
   not exotic: terse results ("Now derives from `--strip`"), edits described in prose, and most
   `verify` phrasings. An agent phrases a claim however it likes; no pattern list closes that.
   It stays as the no-key, no-network fallback, which is a real requirement, not as the default.
2. **Framing beat prompt wording.** Tightening the extraction prompt roughly doubled recall and
   lifted precision, but switching from "extract claims from this report" to "classify each
   sentence" tripled recall again. Classification is an easier task than extraction, and it
   guarantees verbatim spans by construction: the model returns an index, never text, so it
   cannot paraphrase a claim into existence.
3. **An explicit recall-suppressing instruction did real damage.** The first prompt said
   "over-extraction is worse than missing one". For this product the opposite is true at the
   extraction stage: a junk claim gets an `unwitnessed` verdict a human can dismiss, while a
   missed claim is never checked at all. That line is gone.

## Caveats, stated plainly
- **The labels are machine-made.** Same model family as one of the extractors, which biases the
  comparison in its favour. Two runs of the labeller on the same 204 sentences returned 45 and 63
  claims, so the labeller is not even stable against itself. These numbers order the options; they
  do not measure the product. Human labels (`eval/gold/claims_to_label.csv`, three passes, κ) are
  what EVIDENCE_PLAN §2 requires and they are still pending.
- **The corpus is one person's sessions**, weighted toward docs, repo hygiene and planning rather
  than test-and-build coding work. Claim vocabulary on ordinary coding tasks may differ. The fix
  is SWE-chat (see below), not more of the same sessions.

## Next
- Mine claim phrasings at scale from SWE-chat (6,000 real sessions, ODC-BY, auto-gated on Hugging
  Face) to raise the regex fallback's recall and to build a far larger gold set than 10 sessions.
- Cascade worth testing once labels exist: `gpt-5-mini` for recall 0.95, then a stronger model or
  the rules to filter, instead of one model doing both jobs.

---

# False-accusation audit, same day

Rather than label 204 sentences for "is this a claim", we pointed the check at the only verdict
that can harm a user: `contradicted`. Ran the full pipeline over **93 local sessions with a report
and ≥3 tool calls**.

| | before | after |
|---|---|---|
| claims extracted | 122 | 122 |
| accusations (`contradicted`) | 4 | **1** |
| of which false on inspection | ≥3 | 0 known |

All three false accusations had one cause: `_PATH_RE` truncated absolute and dot-prefixed paths, so
`/Users/me/.claude/skills/standup/SKILL.md` was parsed as `claude/skills/standup/SKILL.md`, which of
course does not exist, which became an accusation. The fourth was an extraction error: a restated
task (`Debug the pipeline — …`) typed as an edit claim.

Fixes:
1. `_PATH_RE` now matches absolute, `~`-prefixed and `./`-prefixed paths whole.
2. New `rules.accusable()`: a missing or unchanged file may only support an accusation when the
   repo root exists on this machine, the claim names a directory component (not a bare
   `foo.json` that could live anywhere), and the path is absolute or inside that root. Otherwise
   the verdict is `unwitnessed` — we could not check it, which is not the same as it being false.
3. `_NOT_CLAIM_RE` drops imperative task titles and observed-state phrasing.

**The lesson worth keeping:** 45 minutes × 3 people aimed at the extractor would not have found
this. Thirty seconds aimed at the accusations did. Measure the stage where the product can hurt
someone, not the stage that is easiest to label.
