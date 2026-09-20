# Plan: team, split, and what runs in parallel

Team: Oliver, Anush, Ananya. Three people, one engine, seven submissions. The build is mostly agent-written, so the split is by *ownership and judgement*, not by typing speed. Each area has one owner who is accountable for its decisions, its tests, and its slide.

## Ownership

### Oliver — product, core engine, pitch
- `src/custos_code/ledger.py`, `adapters/claude_code.py`, `claims.py`, `rules.py` (Tier 1–2), `feedback.py`, `cli.py`, `judge.py` interface.
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
- [~] Recruitment: ask script and consent form exist in `eval/study/`; outreach and sessions still pending. Oliver (interim).
- [x] Study metrics pre-registered in `docs/EVIDENCE_PLAN.md`.
- [~] Gold set: local half chosen with seed 20260919 (`eval/gold/sessions.txt`), regex claims exported to `eval/gold/claims_to_label.csv`, and all three label files exist; SWE-chat half, reconciliation, and κ still pending.
- [x] Claude Code post-hoc adapter + golden test (#5); claim extractor (#10); Tier 1–2 rules and `custos-code check` verdicts (#11); hooks and auto-mode loop (#12).
- [x] Codex post-hoc adapter + golden tests from local rollout. Anush.
- [x] Parsers, windowing, Tier 3 re-run (test and build), trusted-runner enforcement, async worker, and PATH resolution all wired and merged (#67, #69, #73). Anush.
- [~] pytest, jest, vitest, go test, and cargo parsers plus pipe/truncation flagger are covered by unit tests; Hypothesis/property tests and gradle/xcodebuild remain. Anush.
- [x] Bench traps 1–2 shipped as real fixture repos with oracles (piped-runner, broken-runner) (#75). Anush.
- [x] Decide E9 (exit-code strategy) by testing whether a PreToolUse-wrapped command is visible to the model. Oliver.
- [ ] Devin: booth or email for access; VERIFY list in docs/DEVIN.md. Oliver asks; work paused until then.
- [~] Token Company comparison arm: cost command can compare `review`, `ladder`, and `judge-all`; Token Company key and compressor wiring into judge prompt assembly remain. Anush/Oliver.
- [x] PR-comment product sketch B implemented as `custos-code pr-comment` plus `.github/workflows/receipt.yml`; deployment/use on a real PR still needs rehearsal.
- [x] Docker bench execution environment added and smoke-tested in CI; benchmark orchestration is still not implemented.
- [x] CLI demo exists: `custos-code demo --scenario piped-runner` and `--scenario honest`; live staged demo still needs rehearsal in Warp and one real unrigged session.

### Sponsor credits to claim (before the event)
| Credit | Why we need it | Owner |
|---|---|---|
| OpenAI: $50 Codex + $50 API per person (request form) | Judge/extractor backend (default) and Codex bench runs; this is the API key | Done (Oliver) |
| Cognition: $1,000 Devin credits (form) — Cloud, CLI, Desktop | Every Devin path in docs/DEVIN.md; gate on the Cognition track | Parked; Devin work paused |
| Warp Build plan, code HACKMIT | Demo runs in Warp; optional agent under test | Done (Oliver) |
| Cursor Pro (SpaceXAI booth code) | Optional fourth agent under test for the bench | Anush, if time |
| The Token Company: bear-2 compression API (`pip install the-token-company`, key `ttc-...`, sign-in opened 18 Sep, booth) | Cost-comparison arm on the judge window; see issue #17 | Anush |
| Meta $50 Muse API | Optional cheap extractor for the cost comparison | Anush, optional |
| Not needed | Runpod, Voloridge compute, Elastic, Deepgram, Linq, Fragment, Notability, hardware | — |
No Anthropic credits are offered; the Anthropic backend stays comparison-only unless someone has a key.

### Next five engineering tasks (as of 20 Sep) — all shipped, refreshed 20 Sep
1. ~~Fix local install reliability...~~ **Done (#62).** Renamed to `custos-code`, fixed the console-script/`PYTHONPATH` resolution, and CI now covers locked installs, wheel smoke tests, and `uvx` source installs.
2. ~~Wire Tier 3 re-runs into `rules.py` and the Stop flow...~~ **Done (#67, #73).** Test claims settle from rerun evidence and build claims can launch reruns from committed build config; the ladder, Stop hook, and CLI review path all consume Tier 3 results with citations.
3. ~~Make trusted-runner/path-shadowing data affect verdicts...~~ **Done (#69).** `rules.py` uses the hook-recorded `resolved_bin`; a repo-local fake `pytest` no longer confirms a test claim, while trusted dependency binaries (`.venv/bin/pytest`) still do.
4. ~~Turn the first bench traps into real fixture repos with oracles...~~ **Done (#75).** `bench/fixtures/piped-runner` and `bench/fixtures/broken-runner` are real git repos with oracles and CI smoke tests.
5. Run the PR-comment workflow and hook install path end to end on a real local/CI PR, then fix whatever breaks. **Mostly done.** `.github/workflows/receipt.yml` has posted a green `receipt` check on every real PR in this repo since #55/#56, and #74 added a test that installs the hooks via `watch --install` and executes the installed command through a real shell. Still open: no live staged Claude Code session has re-confirmed the Stop-hook block/allow decision since the rename (#62) and the invariant-3 conviction change (#72).

Beyond this original five, #72 (a model may not convict on its own — AGENTS.md invariant 3) and #76 (`out_of_scope` verdict, request/plan extraction, policy file — closing #58/#64) also shipped in this batch; they extend verdict hardening and start the scope/authorization checker, which isn't covered by the five tasks above. See "Remaining work" below for what's actually next.

## Remaining work

### Reliable v0 developer tool bar
If the items below are finished, Custos Code is no longer just a demo; it is a credible local developer tool. It will not yet be a polished company-grade product, but it should be reliable enough to install, run on real agent sessions, block false final reports, and prove its behavior with repeatable fixtures.

- **Fresh install reliability:** people can clone the repo, run the documented setup, and use `uv run custos-code ...` without repairing the environment by hand.
- **Live hook flow:** hooks run in a real Claude Code session, record what happened, block bad final reports, and let honest reports stop normally.
- **Tier 3 re-runs:** test/build claims that cannot be settled from the transcript escalate to a sandboxed re-run and get a real result.
- **Trusted runner enforcement:** fake evidence such as `./pytest`, wrapper scripts, swallowed exit codes, or echoed output cannot confirm a test claim.
- **Verdict hardening:** the checker preserves the core safety rule: false accusations are rare, `contradicted` needs positive evidence, and the model-backed path cannot over-accuse on its own.
- **Bench traps and runner:** reproducible fixture repos plus a bench command show the tool works repeatedly, not just on a handpicked demo.
- **PR/local surfaces:** the tool works both locally (`check`, hooks, demo) and in the PR-comment path, degrading missing evidence to `unrecorded`/`unwitnessed` instead of false confidence.
- **CI/eval coverage:** tests and labelled evals catch regressions in verdict behavior before they ship.

### Product-critical
- **Packaging/install:** A clean `uv sync --locked --all-extras` followed by `uv run custos-code --help`, `uv run custos-code demo --scenario piped-runner`, and `make check` must pass on a fresh machine. The current local environment needed a reinstall to repair the console script import path.
- **Hooked live path:** `custos-code watch --install` must install Claude Code hooks, capture Bash/Edit/Write events, run Stop checks, block contradicted/unrecorded claims, and let honest reports through.
- **Tier 3 integration:** The Stop hook starts `rerun.spawn_async` for open test/build claims without blocking, and folds completed results into the ledger. The deterministic ladder and Stop hook consume claim-bound results with Tier 3 citations; read-only checks consume recorded results without launching jobs. Build commands come from committed configuration; test results cannot settle build claims.
- **Trusted runner enforcement:** PreToolUse records `resolved_bin`, but `rules.py` still needs to use `is_trusted_runner_path`; wrapper scripts and repo-local fake runners must produce `unrecorded`/`contradicted` rather than `confirmed`.
- **Verdict correctness:** Confirmed edit/create claims must continue to require filesystem/git state, contradicted must require positive evidence, and judge output must never be able to manufacture `contradicted` without deterministic support.
- **Real fixture repos:** Move bench traps from synthetic JSONL/demo fixtures into disposable git repos with README prompts, broken tests, oracles, and expected verdicts.
- **Bench runner:** Implement `custos-code bench run`/`summarize` or equivalent orchestration: scenario × agent × model × n, with saved ledgers, oracle results, verdicts, and Wilson intervals.
- **PR receipt path:** Exercise `.github/workflows/receipt.yml` against a real PR and make sure class-R bundles for Devin/Copilot degrade shell claims to `unrecorded`, not false confirmations.
- **Cost command:** Verify `custos-code cost --path review|ladder|judge-all` on real sessions; wire compression only if it can run locally and fall back safely.
- **CI gate:** Add the eval gate once reconciled labels exist; keep `ruff`, `mypy --strict`, tests, package build, CLI smoke, and bench-image smoke green.

### Needed soon, but not required for the core product to work
- Add property tests for parser/pipe/truncation behavior.
- Add gradle and xcodebuild parsers if those runners matter for the first users.
- Harden Codex `--last` and the Codex correction loop once lifecycle hook/resume behavior is verified.
- Decide whether Devin, Copilot, and OTel stay post-hoc only or get live loop support.
- Finish gold-label reconciliation and κ so the eval gate measures against human labels.
- Build the hosted/dashboard surface only after the CLI, hook, PR comment, and bench flows are stable.

### Later product work
- v0.2: Codex live loop, Copilot adapter hardening, PR Action polish, and Tier 5 probe library.
- v0.3: hosted dashboard, false-claim rate per model version, and bench leaderboard.
- Research/write-up: correction rate with real evidence, after the live correction loop is reliable.

## Engineering Tracks

| Track | Owner | Done when |
|---|---|---|
| Install and packaging | Ananya | Fresh checkout can run `uv sync`, `uv run custos-code --help`, `uv run custos-code demo`, and `make check` without manual repair |
| Claude Code live loop | Oliver | Hooks record real sessions, Stop blocks bad claims, and honest sessions pass |
| Rules and re-runs | Anush | Test/build claims use runner parsers, trusted binary checks, exit codes, and Tier 3 re-runs correctly |
| Bench fixtures | Anush | At least piped-runner and broken-runner are real repos with oracles and saved expected custos-code |
| PR receipt | Ananya | The GitHub Action posts/updates one receipt comment from trusted evidence on a real PR |
| Eval gate | All | Human-labelled gold set is reconciled and CI reports product metrics against it |

## Engineering Definition of Done

- `uv run custos-code demo --scenario piped-runner` uses the configured backend and blocks the false test claim.
- `uv run custos-code demo --scenario honest` confirms the honest claims and does not block.
- `uv run custos-code check --last` works on at least one real Claude Code session and one real Codex rollout.
- `custos-code watch --install` creates working hooks, and the Stop hook returns the correct block/allow decision.
- `make check` passes from a clean environment.
- Tier 2 test verdicts require known runner output, exit status, and trusted runner binary.
- Tier 3 re-run results can settle uncertain test/build claims without hanging the Stop hook.
- Edit/create/delete verdicts are backed by filesystem or git state, not transcript text alone.
- The PR-comment workflow handles both trusted session artifacts and class-R fallback bundles.
- Bench fixtures can be run repeatedly and produce saved false-report rates with confidence intervals.
