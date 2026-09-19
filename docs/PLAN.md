# Plan: team, split, and what runs in parallel

Team: Oliver, Anush, Ananya. Three people, one engine, seven submissions. The build is mostly agent-written, so the split is by *ownership and judgement*, not by typing speed. Each area has one owner who is accountable for its decisions, its tests, and its slide.

## Ownership

### Oliver — product, core engine, pitch
- `src/receipts/ledger.py`, `adapters/claude_code.py`, `claims.py`, `rules.py` (Tier 1–2), `feedback.py`, `cli.py`, `judge.py` interface.
- The Stop-hook correction loop and the live demo.
- `docs/DESIGN.md`, the pitch, the per-sponsor framing, judge Q&A.
- Final say on verdict semantics and invariants.

### Anush — parsers, bench, eval, cost (deterministic, measurable things)
- Per-runner output parsers (pytest, jest, vitest, go test, cargo, gradle, xcodebuild) and the pipe/truncation flagger.
- Hash chain and integrity (Tier 0).
- Tier 3 sandbox re-run (worktree + subprocess, timeouts, committed config).
- `bench/`: scenarios, fixtures, oracles, runner; per-model false-report rates with confidence intervals.
- `eval/`: metrics (per-class precision/recall, Cohen's κ), the regex baseline, the CI eval gate.
- `cost.py` and the Token Company chart (judge-everything vs ladder vs ladder+compressor, with κ on each).

### Ananya — infra, integrations, and the evidence track
- Repo hygiene, CI (`.github/workflows/ci.yml`), packaging, `uv`, Docker for the bench runner.
- Adapters in build order (`docs/ADAPTERS.md` §6): Claude Code post-hoc (with Oliver), `codex.py` post-hoc, then the class-M recorder, then Devin Path C/A/B when access arrives, then `copilot.py`, `otel.py`.
- The PR-comment GitHub Action (product sketch B).
- Finance reconciliation fixture and, if it fits, the cross-run `memory.md` loop (Maximor).
- **Evidence track (parallel, starts now):** recruit testers, run the study in `docs/EVIDENCE_PLAN.md`, collect donated sessions, coordinate gold-set labelling, gather testimonials, cut the demo video.

### Interim while Ananya is out (from 19 Sep; revert when she is back)
Her calendar-time work cannot wait; her build work can. Redistribution:

| Item | Was | Now | Why |
|---|---|---|---|
| Codex post-hoc adapter (`adapters/codex.py`) | Ananya | **Anush** | Fully specified from a real rollout (docs/ADAPTERS.md §3); it is a parser job, and it feeds the bench runner he owns |
| Docker for the bench runner | Ananya | **Anush** | He owns bench |
| Tester recruitment: ask script, first 15 outreach messages, consent form | Ananya | **Oliver** (start now) | Replies take days; this is the long pole |
| Gold-set session selection (seeded random, 20 sessions) | Ananya (logistics) | **Oliver** | Needed by hour 8 regardless |
| CI, packaging, `uv` | Ananya | **Oliver** (already done for v0) | Only maintenance left |
| Devin Path C adapter, blueprint recorder, API nudge | Ananya | **Paused** until Devin access; then Ananya | Blocked on access anyway |
| Copilot and OTel adapters | Ananya | **Paused** (post-event) | Not on the critical path |
| Finance fixture and memory loop (Maximor) | Ananya | **Paused**; one slide unless she is back by hour 12 | Conditional track |
| PR-comment GitHub Action | Ananya | **Oliver after the hour-10 go/no-go**, else post-event | Demo form B is nice-to-have; form A is the demo |
| Running study sessions, testimonials, demo video | Ananya | **Ananya on return**; Oliver keeps the tracker warm | Needs a person present |

### Shared
- Gold-set labelling: all three label the same 100 claims independently; κ is computed across us.
- Pitch rehearsal: twice, timed, in Warp.
- Every PR gets one human review from a different owner.

## Why this split

- Anush's competitive-programming background is the right fit for parsers, oracles, metrics, and anything that must be exactly right and fast. The bench and eval are where the numbers on the slides come from.
- Ananya's infra and hardware-adjacent SWE background fits CI, sandboxing, adapters, and the Action. She also owns the evidence track because user recruitment and studies take calendar time, not build time, and must run in parallel from day one.
- Oliver owns the product decisions and the demo because he has been in the research and design since the start.

If the split turns out wrong, swap. The point is that every area has exactly one name on it.

## Phases

### Now → event (calendar time; the evidence track is the long pole)
- [x] Repo created, CI green, collaboration rules merged (PR #1); Anush and Ananya are collaborators; CODEOWNERS routes reviews.
- [x] Design doc v0.4, research report, mechanics, adapters, Devin plan, prototype, evidence protocol.
- [ ] OpenAI credits requested by all three (API key + Codex); Devin credits form submitted; Codex CLI and Claude Code logged in.
- [ ] Register for the OpenAI challenge (credits only for submitters). Oliver.
- [ ] Recruitment started: ask script written, first 15 messages sent, consent form drafted. Oliver (interim).
- [x] Study metrics pre-registered in `docs/EVIDENCE_PLAN.md`.
- [ ] Gold set: 20 sessions chosen with a recorded seed (10 local, 10 SWE-chat); ~100 claims extracted; labelling by Oliver and Anush now, Ananya's pass when back. Oliver.
- [ ] Claude Code post-hoc adapter + 3 golden tests. Oliver. Unblocks `receipts check --last`.
- [ ] Codex post-hoc adapter + 3 golden tests from local rollouts. Anush.
- [ ] pytest and jest parsers with hypothesis tests; pipe flagger. Anush.
- [ ] Bench traps 1–2 as real fixture repos with oracles (piped runner, broken runner). Anush.
- [ ] Decide E9 (exit-code strategy) by testing whether a PreToolUse-wrapped command is visible to the model. Oliver.
- [ ] Devin: booth or email for access; VERIFY list in docs/DEVIN.md. Oliver asks; work paused until then.
- [ ] Token Company sign-in; confirm compressor API shape. Anush.

### Sponsor credits to claim (before the event)
| Credit | Why we need it | Owner |
|---|---|---|
| OpenAI: $50 Codex + $50 API per person (request form) | Judge/extractor backend (default) and Codex bench runs; this is the API key | All three, now |
| Cognition: $1,000 Devin credits (form) — Cloud, CLI, Desktop | Every Devin path in docs/DEVIN.md; gate on the Cognition track | Oliver, now |
| Warp Build plan, code HACKMIT | Demo runs in Warp; optional agent under test | Oliver |
| Cursor Pro (SpaceXAI booth code) | Optional fourth agent under test for the bench | Anush, if time |
| Meta $50 Muse API | Optional cheap extractor for the cost comparison | Anush, optional |
| Not needed | Runpod, Voloridge compute, Elastic, Deepgram, Linq, Fragment, Notability, hardware | — |
No Anthropic credits are offered; the Anthropic backend stays comparison-only unless someone has a key.

### Next five, per person (as of 19 Sep)
**Oliver:** (1) Claude Code post-hoc adapter and golden tests; (2) claim extractor with the regex baseline; (3) recruitment messages out and consent form; (4) gold-set selection with seed; (5) E9 experiment on the PreToolUse wrapper.
**Anush:** (1) Codex post-hoc adapter and golden tests; (2) pytest/jest parsers + pipe flagger with property tests; (3) hash-chain verification CLI (`receipts verify-ledger`); (4) piped-runner and broken-runner fixtures with oracles; (5) cost meter skeleton wired to both judge backends.
**Ananya (on return):** (1) Devin Path C adapter; (2) run the first timed-verification sessions with recruited testers; (3) gold-set labelling pass; (4) PR-comment Action; (5) finance fixture only if hour 12 has not passed.

### Event: 24 hours (see `docs/DESIGN.md` §13 for the hour-by-hour)
- Hours 0–10: engine to the go/no-go (contradicted precision on 40 labelled claims).
- Hours 10–18: hooks, correction loop, bench run, prevalence pass, finance fixture if staffed.
- Hours 18–24: renderers, cost chart, demo rehearsal, submissions.

### After
- v0.2: Codex and Copilot adapters solid; GitHub Action; Tier 5 probe library.
- v0.3: hosted dashboard; false-claim rate per model version; bench leaderboard.
- Write-up: correction rate with real evidence (the untested condition in the literature).

## Parallel tracks that take calendar time (start immediately)

| Track | Owner | Why it cannot wait |
|---|---|---|
| Tester recruitment and scheduling | Oliver (interim), Ananya | People take days to reply; sessions take a week to accumulate |
| Session donations | Oliver (interim), Ananya | Real, unrigged sessions are required in every demo ("you planted the lie") |
| Gold-set labelling | All | Needs three independent passes and a reconciliation meeting |
| Sponsor access (OpenAI credits, Devin, Token Company sign-in) | Oliver | Gated by sponsor timelines |
| Bench fixtures | Anush | Traps must be validated to actually elicit the failure before they are trusted |

## Definition of done for the event

- `receipts check --last` works on a real session for all three of us and prints a cited receipt.
- Stop hook blocks a contradicted test claim on a trap and on at least one real session; the agent re-runs and corrects.
- Gold set: 100 claims, κ reported, contradicted precision ≥ 0.90 or the pitch narrowed to Tiers 1–3.
- Bench: ≥ 4 traps × 2 agents × 10 runs, rates with 95% CIs.
- Cost chart on 50 sessions.
- Evidence: ≥ 8 testers through the timed verification task; time-to-decision with and without Receipts.
- Submitted to: Warp, OpenAI, Token Company, Ramp, Long Lake, general; Cognition if Devin access lands; Maximor only if Ananya is back and the memory loop fits.
