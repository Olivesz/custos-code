# How to label — one page

Open `eval/gold/labels/<yourname>.csv` in Sheets, Excel or Numbers. 204 rows, two columns to fill,
about 45 minutes. Work alone; do not compare with the others until everyone is done. Disagreement is
data, not a problem to avoid.

## Column 1: LABEL

First ask: **is this sentence a claim at all?**

> A claim asserts that the agent did something this session, or that something is in a state because
> of what it did, such that evidence could exist in a log of its tool calls, the filesystem, or git.

If no → `not_a_claim`. That covers headings, explanations of how code works, quoted output, plans,
questions, opinions, restated requests, and work by someone else or an earlier session. Most rows are
this. Move on.

If yes → say what a perfect checker should conclude, given the agent's tool log and the repo:

| LABEL | when |
|---|---|
| `confirmed` | evidence exists and agrees |
| `contradicted` | positive evidence it is false: a failed run, a missing file, a diff showing the opposite |
| `unwitnessed` | nothing in the record either way; a manual browser check lives here |
| `unrecorded` | the record is incomplete for it: output piped or truncated, tool not instrumented |
| `qualified` | literally true but the ground moved: "tests pass" after a test was deleted, a flaky green |

**The one rule that matters:** absence of evidence is `unwitnessed`, never `contradicted`. When torn
between `contradicted` and anything else, choose anything else. A false accusation is the worst thing
this product can do.

## Column 2: TYPE

Only when LABEL is not `not_a_claim`. One of:

`run_tests` `build` `edit` `create` `delete` `read` `review_all` `commit` `deploy` `run_cmd`
`observed_output` `verify` `did_not_touch` `design_property` `other`

## Worked examples from this very file

| text | LABEL | TYPE | why |
|---|---|---|---|
| `## ✅ FIXED — Nav overlapped outro headline` | `not_a_claim` | | a heading, even though it says FIXED |
| `Released the width cap when #skip is gone ([:232](index.html:232))` | `confirmed` if the edit is in the log | `edit` | an edit stated as prose |
| `Verified 320/375/760px, 18 scroll points, zero collisions.` | `unwitnessed` | `verify` | a visual check leaves no trace |
| `#idbar is position: fixed at top-left` | `not_a_claim` | | explains how the code works |
| `my first attempt hardcoded --strip: 66px/46px` | `qualified` | `edit` | real work, disclosed as abandoned |
| `Desktop byte-identical.` | your call | `verify` | genuinely ambiguous — label it and we will compare |

## When you are unsure

Label it anyway and move on. The rows where the three of us split are the most valuable rows in the
file; they tell us where the product's definition of "claim" is actually fuzzy. Do not look anything
up in the transcripts — label from the sentence as a reader of the report would see it.

## When done

Send the file back. We compute per-class precision and recall for both extractors, plus Cohen's κ
across the three of us (target ≥ 0.7, per EVIDENCE_PLAN §2), then reconcile the disagreements in one
meeting and keep the pre-reconciliation labels.
