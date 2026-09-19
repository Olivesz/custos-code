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
