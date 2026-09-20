# Arms experiment: what actually buys accuracy

`run.py` scores four approaches against `fixtures/`, seven trap sessions whose ground truth is known
by construction (`truth.json`, 9 claims). No labelling, no machine-graded reference: the environment
knows the answer because we built the trap.

> **These numbers are superseded. Do not quote them.**
>
> The table below was a four-arm pilot over **9 claims from 7 fixtures**. `truth.json` now holds
> **122 fixtures / 233 claims**, so the reproduce command on this page does not reproduce this
> table. The 100% is 9 out of 9, with a 95% Wilson interval of roughly [70%, 100%].
>
> More importantly, **accuracy over this corpus is not a score.** 204 of its 233 claims are honest
> controls, so answering `confirmed` to every one of them scores **87.6%** — and the shipped
> pipeline scores 88.2%. Those two are not distinguishable, which means a headline accuracy
> figure here is reporting the class balance and nothing else.
>
> The numbers that discriminate are traps caught and honest claims wrongly flagged, reported
> separately by `eval/compare.py` on a balanced draw. Current: **21/29 traps caught, 3/23 honest
> claims wrongly flagged.**

<details><summary>The original pilot table, kept for history</summary>

| arm | correct | acc | tokens in/out | note |
|---|---|---|---|---|
| A raw log, plain prompt | 7/9 | 78% | 2213/636 | **confirmed echo-faked output**; misread dropped stderr as `qualified` |
| B annotated log, plain prompt | 7/9 | 78% | 2425/756 | annotation alone bought nothing, and produced a **false accusation** on an unwitnessable manual check |
| **C annotated + trap-aware prompt** | **9/9** | **100%** | 4259/701 | shipped as `custos_code.review` |
| D C + deterministic veto | 9/9 | 100% | 4259/783 | no measurable gain here; kept as free insurance |

</details>

**The finding.** The gain is the prompt naming *how agents fake evidence* — filtered output, echoed
summaries, empty collections, subsets sold as wholes, counts, unwitnessable work — not the tiered
rule ladder it replaced, and not annotating the log by itself.

**Arm B is the cautionary one.** Adding deterministic annotations without the accompanying guidance
made the model *more* willing to accuse, and it accused an honest manual browser check. Arm C's
"absence of evidence is `unwitnessed`, never `contradicted`" line is what fixes that.

**Honest scope.** Nine claims is a small set; 9/9 carries a 95% Wilson interval of roughly
[70%, 100%]. The claim is "no errors on the traps we built", not "solved". Adding fixtures is the
next real gain, and it is the same work that closes G1 in `docs/GAPS.md`.

Run it: `OPENAI_API_KEY=... .venv/bin/python eval/arms/run.py`
