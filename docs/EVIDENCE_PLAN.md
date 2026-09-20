# Evidence plan: proving it works, saves time, and saves money

We are optimising for usability and for whether this actually works as a product. A good demo is not enough; we want a body of evidence a skeptical judge or a first customer cannot wave away. Everything below is pre-registered: metrics and thresholds are written here *before* data is collected, and we report what we get.

Owner: Ananya (recruitment, scheduling, running sessions). Metrics computed by Anush. Oliver writes the results section.

## Four kinds of evidence

### 1. The problem is real and common (prevalence)
- **Bench:** per-model false-report rate on the reproducible traps (`bench/`), N ≥ 10 runs per trap per model, 95% Wilson intervals. Report `qualified` separately from `contradicted`.
- **Wild:** Custos Code run over a SWE-chat sample (≥ 300 sessions) and over donated sessions. Report the share of sessions with ≥ 1 contradicted claim, ≥ 1 unwitnessed claim, ≥ 1 pipe/truncation flag. Report conditional-on-failure rates where the session outcome is known.
- **Honest framing:** the wild per-session rate of a provably false claim may be low. We report it either way.

### 2. The checker is right (accuracy)
- **Gold set:** 100 claims from 20 sessions (10 local, 10 SWE-chat), labelled independently by all three of us using `eval/gold/LABELLING_GUIDE.md`. Reconcile disagreements in one meeting; keep the pre-reconciliation labels for κ.
- **Metrics:** per-class precision and recall; F1; Cohen's κ (each of us vs majority, and judge vs majority); the regex baseline on the same set.
- **Thresholds (pre-registered):** contradicted precision ≥ 0.90; extraction recall ≥ 0.85; judge κ ≥ 0.70. If contradicted precision < 0.90, the product narrows to Tiers 1–3 and we say so.
- **Negative set:** ≥ 20 honest-but-unverifiable cases (manual browser checks, tests run in a watcher, Makefile aliases, CI-run tests, work from a previous session). Zero contradicted verdicts allowed on these.
- **Determinism:** judge at temperature 0, three samples; report the disagreement rate between samples.

### 3. It saves time and money (the product claim)
**Timed verification task (within-subject, counterbalanced).**
- Participants: 8–12 developers who use Claude Code, Codex, Cursor, or Copilot agent at least weekly.
- Materials: 6 real or bench sessions, each with the agent's report and the repo state. Half contain at least one false or qualified claim; half are honest.
- Task: "Decide whether you would merge this. Say which claims in the report you trust." Once with only the report and the repo (manual verification), once with the Custos Code output. Order and session assignment counterbalanced.
- Measures: time to decision (seconds); decision accuracy against ground truth; claims correctly flagged; confidence (1–5).
- Pre-registered hypothesis: median time to decision falls by ≥ 40% with Custos Code, and accuracy does not fall.
- Cost: `custos-code cost` per session, reported next to the time saved; compare to the participant's own estimate of their hourly cost only if they volunteer it.

**One-week install (retention).**
- Same participants install the Stop hook for one week.
- Measures: sessions checked; blocks fired; corrections observed; false blocks reported; whether the hook is still installed at day 7; one question: "would you be disappointed if this went away?" (yes/no).
- Pre-registered threshold: ≥ 60% still installed at day 7 with zero unresolved false blocks.

### 4. People can use it (usability)
- 5 think-aloud sessions on the CLI receipt and the PR comment, 20 minutes each. Record where the eye goes first, what is misread, what they want to click.
- System Usability Scale (10 questions) after the timed task. Target ≥ 70.
- Fix the top three findings before the demo; keep the before/after screenshots.

## Recruitment

Channels, in order of expected yield:
1. HackMIT attendees and MIT peers who use coding agents daily (fastest).
2. The developers who already wrote about this problem and built partial tools: the authors of the 249-session audit, red-handed, TruthGuard, Passproof, "i-dont-believe-you", the piped-runner post. They are expert testers and credible quotes.
3. r/ClaudeAI, r/cursor, r/ChatGPTCoding posts asking for session donations (once Reddit is reachable from our tooling; post manually otherwise).
4. Hacker News "Show HN" after the event.
5. Discord servers for Claude Code, Cursor, Codex (ask, do not scrape).

Script for the ask (keep it under 60 words): what it is, that it runs locally, that we redact secrets at ingest, that we need 20 minutes and, optionally, five recent session logs, and what they get (their own custos-code, early access, a name in the thanks if they want it).

## Consent and data handling
- Written consent for the timed task and the install week; participants can withdraw and have their data deleted.
- Donated sessions are redacted at ingest (secrets, tokens, URLs with credentials) before anything is stored; raw files are deleted after redaction.
- Only aggregate numbers and anonymised quotes appear in the deck or the repo.
- No minors; no customer data from employers without their permission.

## What goes on the slides (and nowhere else if we did not measure it)
1. Per-model false-report rate on the traps, with intervals.
2. Gold-set precision/recall/κ, baseline vs judge.
3. Median time-to-decision with vs without Custos Code, n, and accuracy.
4. Retention at day 7 and the number of real corrections observed.
5. Cost per session by tier.

## Logistics
- Tracker: `eval/study/participants.csv` (id, channel, agent used, scheduled, consented, task done, install done, day-7 check) — ids only, no names in the repo.
- Session artefacts: `eval/study/sessions/<participant-id>/` gitignored; only derived metrics are committed.
- Calendar: start recruiting on day one; run timed tasks on a rolling basis; the install week must start ≥ 7 days before the pitch.
