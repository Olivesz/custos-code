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
- [x] Repo created; collaboration rules, CODEOWNERS, and CI workflow committed. Current remote CI status and collaborator access were not rechecked in the 19 Sep audit.
- [x] Packaging metadata, Makefile targets, and pre-commit configuration committed. Local environment setup and passing checks remain to be verified (see audit below).
- [x] Design doc v0.4, research report, mechanics, adapters, Devin plan, prototype, evidence protocol.
- [ ] Keys set (OpenAI, Anthropic) for Oliver and Anush; Codex CLI and Claude Code logged in.
- [ ] Register for the OpenAI challenge (credits only for submitters). Oliver.
- [ ] Recruitment started: ask script written, first 15 messages sent, consent form drafted. Ananya.
- [x] Study metrics pre-registered in `docs/EVIDENCE_PLAN.md`.
- [ ] Gold set: 20 sessions chosen with a recorded seed (10 local, 10 SWE-chat); ~100 claims extracted; all three label independently. Ananya coordinates selection and labelling.
- [x] Claude Code post-hoc adapter + 3 adapter tests (one golden comparison, flags/redaction, latest-session lookup), merged in PR #5 (`791a28b`). Oliver. `receipts check --last` displays the ledger summary and report; verdicts are not implemented. Tests were not successfully rerun in the 19 Sep audit (see below).
- [x] Hash-chain construction and verification, basic secret redaction, and a chain/tamper unit test implemented. SQLite persistence and the verification CLI remain unfinished.
- [ ] Codex post-hoc adapter + 3 golden tests from local rollouts. Ananya.
- [ ] pytest and jest parsers with hypothesis tests; pipe flagger. Anush.
- [ ] Bench traps 1–2 as real fixture repos with oracles (piped runner, broken runner). Anush.
- [ ] Decide E9 (exit-code strategy) by testing whether a PreToolUse-wrapped command is visible to the model. Oliver.
- [ ] Devin: booth or email for access; VERIFY list in docs/DEVIN.md. Oliver asks; work paused until then.
- [ ] Token Company sign-in; confirm compressor API shape. Anush.

### Repository audit (19 Sep; Ananya back)

Ananya's original ownership above is restored. This audit covers the current checkout through `7ebc42d`; unchecked external tasks are not evidence that nobody has done them.

| Area | Evidence and remaining work |
|---|---|
| Documentation and prototype | Design v0.4, research, mechanics, adapter/Devin specs, study protocol, and HTML prototype are present. The prototype is a mock, not an engine integration. |
| Infrastructure | CI runs lint, types, and tests; packaging and developer commands exist. No committed uv lockfile or bench Dockerfile was found. The eval CI gate is still a comment. |
| Claude Code and ledger | Adapter, CLI ledger/report display, hashing, and basic redaction are implemented. There are three adapter tests and one hash-chain test; only one adapter fixture has an expected golden ledger. |
| Engine and live loop | Claim extraction, rules, verdict orchestration, judge backends/windowing, runner parsing, re-execution, feedback, and ledger persistence are stubs. Hook scripts call an unimplemented `_hook` command; `watch` and `cost` exit with code 2. |
| Other integrations | Codex, Devin, Copilot, and OTel adapters are stubs. No implemented universal recorder, PR-comment Action, finance fixture, or cross-run memory loop was found. |
| Bench, eval, and study | One piped-runner scenario YAML and the labelling guide exist. No implemented trap fixture repos/oracles, gold-set labels, study results, or cost results were found in the checkout. Recruitment, donations, access, registrations, and submissions need owner confirmation. |

Local verification attempted on 19 Sep:

- `make check` exited 2 at the lint step: `make: uv: No such file or directory`. Lint, type checking, and tests did not run through this target.
- `PYTHONPATH=src python3 -m pytest -q` exited 2 with two collection errors: the available Python 3.10.16 cannot import `enum.StrEnum`. The project requires Python >=3.12. No tests passed or failed execution in this attempt; collection failed.
- Current remote CI status was not verified. These environment failures do not establish whether the implementation passes under its required environment.

### Next five, per person (as of 19 Sep; ownership restored)
**Oliver:** (1) claim extractor with the regex baseline; (2) Tier 1–2 rules and verdict orchestration; (3) E9 experiment on the PreToolUse wrapper; (4) live hooks and correction loop; (5) sponsor access and demo preparation.
**Anush:** (1) pytest/jest parsers + pipe flagger with property tests; (2) hash-chain verification CLI (`receipts verify-ledger`); (3) piped-runner and broken-runner fixtures with oracles; (4) eval metrics and baseline comparison on the shared gold set; (5) cost meter wired to both judge backends.
**Ananya:** (1) recruitment ask, consent form, and scheduling; (2) Codex post-hoc adapter and golden tests; (3) seeded gold-set selection and coordination of all three labelling passes; (4) local uv setup/CI verification and Docker for the bench runner; (5) run the first timed-verification sessions when the checker and study materials are ready. Then follow the adapter build order (Devin requires access), build the PR-comment Action, and take the finance/memory track only if time permits.

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
- Submitted to: Warp, OpenAI, Token Company, Ramp, Long Lake, general; Cognition if Devin access lands; Maximor only if the memory loop fits.
