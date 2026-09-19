# Open questions and decisions still needed

Every item has an owner and a status. Resolve by editing this file; non-obvious resolutions also get a short ADR in `docs/adr/`. The code carries `NEEDS-DECISION(owner):` tags for the same items where they bite.

Status: `open` · `decided` · `deferred (post-event)`

## Product and verdict semantics
| # | Question | Owner | Status | Notes |
|---|---|---|---|---|
| P1 | Should `unwitnessed` ever block the Stop hook? | Oliver | open | Default no. Enterprises may want yes for "deployed" claims. |
| P2 | What does `receipts check --last` show on an honest session, and is that a product moment? | Oliver | open | Answer in the pitch: the cited receipt itself, plus pipe/truncation flags. Do not promise a catch. |
| P3 | Feedback channel to the agent: tool result, user message, or system block? | Oliver | open | Literature says domain-dependent. Test all three on the traps; pick by correction rate. |
| P4 | Product name. "Receipts" is a placeholder. | All | open | Decide before the README goes public. |
| P5 | Scope of `qualified`: which evidence changes count (test deleted, renamed, assertion removed, flaky)? | Oliver | open | See DESIGN §4. Needs a rule list, not a judge. |
| P6 | Tier 5 intent coverage: in scope for the event or post-event? | Oliver | open | Recommendation: post-event, one slide only. |
| P7 | Inline receipt: which overlay surface first (agent re-emit, hook systemMessage, PR comment, Warp block)? | Oliver | open | See DESIGN §6G. Recommendation: hook systemMessage for the event; agent re-emit in auto mode. |
| P8 | Auto mode retry cap and the "new evidence required" rule: how to detect that a retry added evidence for a specific claim? | Oliver | open | Ledger events after the block whose paths/commands match the claim's objects. Cap default 2. |

## Engine
| # | Question | Owner | Status | Notes |
|---|---|---|---|---|
| E1 | Ledger windowing for the judge: last N events plus path-matched events, or full session? | Anush | decided | Hybrid: last N (default 40) union events whose paths/command mention a claim object; sidechain events dropped first. Implemented in `judge.window`. `n` still needs tuning against κ on the gold set. |
| E2 | Which runners get parsers first? | Anush | decided | pytest, jest, vitest, go test, cargo, in that priority order (`parsers.PARSERS`). Others fall to `unrecorded`. |
| E3 | Tier 3 re-run: worktree + subprocess, or Docker? | Anush | decided | Worktree for the event (`rerun.rerun_tests`: HEAD worktree overlaid with the live working tree, so uncommitted edits count); Docker stays for the bench runner. |
| E4 | Tier 3 time budget in the Stop hook (10 s hook limit) | Anush | open | Rules block synchronously; re-run async, result posted as a follow-up. |
| E5 | How to detect a wrapper script named like a runner (`./pytest`, PATH shadowing)? | Anush | open | Resolve binary path; require it outside the repo tree or in a known venv. |
| E6 | Claim extraction: LLM structured output vs regex for mechanical types? | Oliver | open | Measure recall on the gold set at hour 10. Regex may win for test/edit claims. |
| E7 | Judge backend default | Oliver | decided | OpenAI SDK default, Anthropic behind the same interface. |
| E8 | Hash chain: keep, given the model has no write path anyway? | Anush | open | Cheap; keep for tamper-evidence of the stored file, but do not oversell it. |

## Adapters and integrations
| # | Question | Owner | Status | Notes |
|---|---|---|---|---|
| A1 | Codex rollout JSONL field mapping | Ananya | open | Need three real rollouts to write the golden test. |
| A2 | Devin: does hackathon access include an org API token? | Ananya | open | Public API exposes metadata, chat messages, structured_output, PR list only. Adapter = PR + structured_output + CI log. |
| A3 | Copilot session log format and stable link from a PR | Ananya | open | Documented as attached to commits; verify the export. |
| A4 | Cursor: SpecStory export vs hooks | Ananya | deferred (post-event) | |
| A5 | `transcript_path` lag in Claude Code hooks | Oliver | open | Read the file after a short retry; treat missing tail as `unrecorded`. |

## Bench and eval
| # | Question | Owner | Status | Notes |
|---|---|---|---|---|
| B1 | Do the traps elicit the failure reliably across models, or mostly measure prompt sensitivity? | Anush | open | Validate each trap with ≥ 10 runs on two models before it counts. Report prompt variants. |
| B2 | Negative set size and composition | Anush | open | ≥ 20 honest-but-unverifiable cases; see EVIDENCE_PLAN. |
| B3 | Which public datasets actually ship trajectories? | Ananya | open | SWE-chat yes. Replication package: labels only. OverclaimBench, tau2, Terminal Wrench: confirm. |
| B4 | Gold-set session selection: how to avoid picking sessions where we already know the answer? | Oliver | open | Random sample from the 84 local + random SWE-chat; record the seed. |

## Evidence and users
| # | Question | Owner | Status | Notes |
|---|---|---|---|---|
| U1 | Tester count and channels | Ananya | open | Target 8–12; see EVIDENCE_PLAN. |
| U2 | Consent form text | Ananya | open | Short, plain, withdrawal clause. |
| U3 | Can we get session donations before the event? | Ananya | open | Ask MIT peers first. |
| U4 | Is a one-week install feasible before the pitch date? | Ananya | open | Depends on the event date (L1). |

## Logistics and prizes
| # | Question | Owner | Status | Notes |
|---|---|---|---|---|
| L1 | Event date and pitch time | Oliver | open | Drives the evidence calendar. |
| L2 | OpenAI challenge registration (credits only for submitters) | Oliver | open | Do first. |
| L3 | Token Company sign-in (opens Friday the 18th) | Anush | open | Needed for the compressor comparison. |
| L4 | Maximor: staff it (needs cross-run memory, 4–5 h) or one slide? | All | open | Default: one slide unless a fourth person appears. |
| L5 | Repo public or private, and when | Oliver | open | Private until the attribution and secrets scan passes; public for the OSS-maintainer angle. |
| L6 | License | Oliver | open | MIT proposed. |

## Research gaps carried forward
- Reddit was unreachable from our tooling; the complaint corpus skews to GitHub and HN. Post manually if we want Reddit voice.
- The "29–30% internal false-claim rate" figure has no traceable source. Never cite it.
- The correction rate with real evidence is unmeasured in the literature. Ours will be small-n.
- Pricing and model IDs in the design doc are not researched figures. Check the price list before the cost slide.
