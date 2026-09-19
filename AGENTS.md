# AGENTS.md — read this first

This file is for any coding agent (and any human) working in this repo. It says what we are building, why, what must never change, where things live, and how to flag something that still needs a decision.

## What this is

**Receipts** checks a coding agent's final report against the log of what the agent actually did.

A coding agent finishes and says "implemented the feature, ran the tests, all passing." Receipts reads the harness-written action log (tool calls, results, exit codes, file paths, timestamps), splits the report into atomic claims, and gives each claim a verdict with cited evidence. Contradictions are handed back to the agent so it can fix itself, and we measure whether it does.

The problem, precisely: *a coding agent's final natural-language report asserts an action, a state, or a verification that the tool log, the filesystem, git, or the test runner either contradicts or never witnessed.* Keep it that narrow. We do not judge whether code is good. We judge whether the evidence the agent invokes exists and says what the agent says it says.

Why it matters (every number has a denominator; see `docs/RESEARCH.md`):
- 22.58% of 16,118 validated misalignment episodes across 20,574 real sessions are inaccurate self-reporting, and the share is rising (arXiv 2605.29442).
- Only 9.33% of those episodes were resolved in-session; 91.49% of resolutions needed the developer to push back; 2.99% were self-corrected.
- Every vendor attaches an action log. None reads it against the report.

## Invariants (do not change without an ADR in `docs/adr/`)

1. **The ledger is written by the harness, never by the model.** Hooks and adapters only. If a change lets model output into the ledger unflagged, it is wrong.
2. **`contradicted` requires positive evidence** (a failed exit code, a read that never happened, a diff that shows the deletion). Absence of evidence is `unwitnessed`, never `contradicted`.
3. **The LLM judge cannot emit `contradicted`.** Only rules (Tier 1–2), re-runs (Tier 3), and state checks can. The judge emits `confirmed` or `unwitnessed`, at temperature 0, with cited ledger line numbers, or abstains.
4. **Two-evidence rule for anything that blocks:** transcript plus current state (filesystem, git, or re-run) must agree.
5. **Edit/create claims are never confirmed from the transcript alone** (harness bugs can log a Write that never persisted). Filesystem or git must agree.
6. **Tool outputs are data, not instructions.** Nothing in a ledger entry may steer the judge or the extractor.
7. **Report the tier and method on every verdict.** A reader must be able to tell whether a rule, a re-run, or a judgement is speaking.
8. **Ship the dumb baseline beside the judge.** Every eval reports the regex baseline on the same gold set.
9. **Local by default.** Nothing leaves the machine unless the user turns on upload. Redact secrets at ingest, before hashing.

## Verdict vocabulary

| Verdict | Meaning |
|---|---|
| `confirmed` | Evidence exists and agrees. Tier and citations attached. |
| `contradicted` | Positive evidence of absence or failure. Blocks by default. |
| `unwitnessed` | Nothing in the record either way. Not an accusation. Manual browser checks live here. |
| `unrecorded` | The record is known-incomplete for this claim (piped output, truncation, uninstrumented tool). Means "fix your instrumentation." |
| `qualified` | Literally true but the evidence changed under it ("tests pass" after a test was deleted; a suite that passed 1 of 3 re-runs). Reported with the qualifier, never confirmed. |

The taxonomy corroborated / unwitnessed / unrecorded comes from ASSERT-KTH `agent-trace`; credit it where it appears.

## The verification ladder

Every claim is pushed up until a tier settles it.

- **Tier 0 integrity** — hash chain intact, harness-written, completeness known.
- **Tier 1 witnessed** — did the claimed action occur (tool, path, command pattern, timestamp). Deterministic.
- **Tier 2 outcome** — did it succeed the way the report says (exit code, parsed runner summary, pipes flagged, wall-clock plausibility). Deterministic. Requires known runner binary + runner-format output + runner exit code.
- **Tier 3 re-execution** — replay the claimed check in a sandbox. On by default for test and build claims under 60 s, using committed test config.
- **Tier 4 grounded judgement** — semantic claims; model must cite or abstain; cannot contradict.
- **Tier 5 intent coverage** — request/plan → requirements → claims and diff hunks; unrequested work flagged; design properties turned into probes or marked "needs human".

## Repo map

```
AGENTS.md                 this file
README.md                 quickstart and status
docs/DESIGN.md            the design document (generated from docs/design-doc.html)
docs/RESEARCH.md          research report: prevalence, cost, workarounds, landscape, 40 seed cases
docs/research/            the five research notes and the coded complaint corpus
docs/PLAN.md              team split, milestones, what runs in parallel
docs/EVIDENCE_PLAN.md     how we prove it works and saves time: pre-registered metrics, user study
docs/OPEN_QUESTIONS.md    every unresolved decision, with owner and status
docs/adr/                 one file per non-obvious decision
src/receipts/             the engine (see module docstrings)
hooks/                    Claude Code PostToolUse and Stop hook scripts
bench/                    FalseReportBench: scenarios, fixtures, oracles, runner
eval/                     gold set, labelling guide, metrics, baseline
tests/                    unit, golden (real transcripts in → expected ledger out), e2e (traps)
pilots/                   throwaway scripts from the feasibility week
```

## Flagging things that need a decision

Anywhere in code or docs, use a greppable tag with an owner:

```
# NEEDS-DECISION(oliver): should unwitnessed ever block the Stop hook?
```

`grep -rn "NEEDS-DECISION" .` is the running list. When a tag is resolved, move the decision to `docs/adr/` (if non-obvious) or just delete the tag, and update `docs/OPEN_QUESTIONS.md`.

## Conventions

- Python 3.12, `uv`, Pydantic v2 for every type, Typer CLI, Rich tables. `ruff` and `mypy --strict` clean.
- Tests: unit for rules, parsers, hashing; golden-file per adapter; e2e on bench traps. The eval suite is a CI gate.
- Commits: conventional commits (`feat:`, `fix:`, `docs:`, `bench:`, `eval:`). One author identity per person, real name and email. **No tool attribution lines or trailers in commit messages, ever.**
- Every verdict-affecting change updates or adds a gold-set case.
- Model IDs and prices go in `config.toml`, never hard-coded; both OpenAI and Anthropic backends must keep working behind `judge.Backend`.
- Do not paste secrets, tokens, or real customer data into fixtures. Fixtures are synthetic.

## How to run (target; not all of it exists yet)

```
uv sync
uv run receipts check <session.jsonl>       # verdict table for one session
uv run receipts check --last                 # your most recent Claude Code session
uv run receipts watch                        # install hooks for live sessions
uv run receipts bench run --agent claude-code --model <id> --n 10
uv run receipts eval                         # gold set, baseline vs judge, κ
uv run receipts cost <session.jsonl>         # tokens and dollars by tier
```

## What to read before changing behaviour

1. `docs/DESIGN.md` §4 (how verification works) and §16 (what was attacked and what changed).
2. `docs/OPEN_QUESTIONS.md`.
3. The module docstring of whatever you are touching.
