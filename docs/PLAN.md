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
- Adapters: `codex.py`, `copilot.py`, `devin.py` (PR + structured_output + CI log), `otel.py`.
- The PR-comment GitHub Action (product sketch B).
- Finance reconciliation fixture and, if it fits, the cross-run `memory.md` loop (Maximor).
- **Evidence track (parallel, starts now):** recruit testers, run the study in `docs/EVIDENCE_PLAN.md`, collect donated sessions, coordinate gold-set labelling, gather testimonials, cut the demo video.

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
- [ ] Repo shared; keys set (OpenAI, Anthropic); Codex CLI and Claude Code logged in for all three.
- [ ] Register for the OpenAI challenge early (credits only for submitters).
- [ ] Recruit 10–12 testers (Ananya). Get 5 donated Claude Code or Codex session logs per tester where possible.
- [ ] Pre-register the study metrics in `docs/EVIDENCE_PLAN.md` (before any data is collected).
- [ ] Gold set: pick 20 sessions (10 local, 10 SWE-chat), extract ~100 claims, label independently.
- [ ] Confirm at the Cognition booth or by email: Devin credits and whether an org API token comes with hackathon access.
- [ ] Bench traps 1–4 specified as fixtures with oracles (piped runner, broken runner, ghost write, cancelled batch).

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
| Tester recruitment and scheduling | Ananya | People take days to reply; sessions take a week to accumulate |
| Session donations | Ananya | Real, unrigged sessions are required in every demo ("you planted the lie") |
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
- Submitted to: Warp, OpenAI, Token Company, Ramp, Long Lake, general; Cognition and Maximor if their conditions are met.
