# Open questions and decisions still needed

Every item has an owner and a status. Resolve by editing this file; non-obvious resolutions also get a short ADR in `docs/adr/`. The code carries `NEEDS-DECISION(owner):` tags for the same items where they bite.

Status: `open` · `decided` · `deferred (post-event)`

## Product and verdict semantics
| # | Question | Owner | Status | Notes |
|---|---|---|---|---|
| P1 | Should `unwitnessed` or `unrecorded` ever block? | Oliver | decided | Manual mode: nothing blocks, all marks shown. Auto mode: the loop runs until no ✗, no ○, and no bare ? remain (withdrawn or made checkable), cap 3, then hand-back. See DESIGN §6. |
| P2 | What does `receipts check --last` show on an honest session, and is that a product moment? | Oliver | open | Answer in the pitch: the cited receipt itself, plus pipe/truncation flags. Do not promise a catch. |
| P3 | Feedback channel to the agent: tool result, user message, or system block? | Oliver | open | Literature says domain-dependent. Test all three on the traps; pick by correction rate. |
| P4 | Product name. "Receipts" is a placeholder and now collides with a crowded signed-receipt category (see docs/research/competitors_2026-09.md). | All | open | Candidate: **Witness**. Decide before the README goes public. |
| P5 | Scope of `qualified`: which evidence changes count (test deleted, renamed, assertion removed, flaky)? | Oliver | open | See DESIGN §4. Needs a rule list, not a judge. |
| P6 | Tier 5 intent coverage: in scope for the event or post-event? | Oliver | open | Recommendation: post-event, one slide only. |
| P7 | Inline receipt: which overlay surface first (agent re-emit, hook systemMessage, PR comment, Warp block)? | Oliver | open | See DESIGN §6G. Recommendation: hook systemMessage for the event; agent re-emit in auto mode. |
| P8 | Auto mode: detecting that a retry added evidence for a specific claim; nudge templates per verdict type; cap default | Oliver | open | Ledger events after the nudge whose paths/commands match the claim's objects. Templates are deterministic (zero LLM tokens). Cap default 3, configurable. |

## Engine
| # | Question | Owner | Status | Notes |
|---|---|---|---|---|
| E1 | Ledger windowing for the judge: last N events plus path-matched events, or full session? | Anush | decided | Hybrid: last N (default 40) union events whose paths/command mention a claim object; sidechain events dropped first. Implemented in `judge.window`. `n` still needs tuning against κ on the gold set. |
| E2 | Which runners get parsers first? | Anush | decided | pytest, jest, vitest, go test, cargo, in that priority order (`parsers.PARSERS`). Others fall to `unrecorded`. |
| E3 | Tier 3 re-run: worktree + subprocess, or Docker? | Anush | decided | Worktree for the event (`rerun.rerun_tests`: HEAD worktree overlaid with the live working tree, so uncommitted edits count, command auto-detected from committed config markers with an optional override); Docker stays for the bench runner. |
| E4 | Tier 3 time budget in the Stop hook (10 s hook limit) | Anush | decided | Rules block synchronously; re-run async, result posted as a follow-up. Merged (#13): `rerun.spawn_async`/`poll`/`load_result`, entry point `receipts _hook rerun-worker`, dispatched by `hooks.main`. Remaining gap: `hooks.on_stop` runs `claims.extract`/`verdicts.run` for real (no `NotImplementedError` left there) but never calls `spawn_async` — `rules.py`'s `rule_run_tests` has no Tier-3 escalation path, so no claim triggers an async re-run today. That integration was out of scope for E4/E5. VERIFY, none resolved: (1) does Claude Code kill the Stop hook's process group on exit; (2) does a `start_new_session=True` child survive that; (3) revisit if a result file is ever observed to go missing. |
| E5 | How to detect a wrapper script named like a runner (`./pytest`, PATH shadowing)? | Anush | decided | Resolve binary path; require it outside the repo tree or in a known venv. Codex gives argv directly. Merged (#13): `parsers.wrap_command_for_resolution`/`strip_and_parse_trailer` (PreToolUse trailer, piggybacked on E9's exit-code capture) and `is_trusted_runner_path` (repo-tree + venv/node_modules allowlist + committed-config override), wired into `hooks.on_pre_tool_use`/`on_post_tool_use` (the latter threads the resolved binary onto the CALL event's `input.resolved_bin`). Remaining gap, same shape as E4: `rules.py`'s `rule_run_tests` never calls `is_trusted_runner_path` or reads `resolved_bin`, so an untrusted binary doesn't affect any verdict yet. **Issue #22, parsers.py side landed**: `is_trusted_runner_path` now takes `cwd` and resolves a relative `resolved_bin` against it (fails closed, i.e. untrusted, if `cwd` is missing) instead of the calling process's own ambient cwd -- fixes the bug where a relative path (`command -v` returns e.g. `.venv/bin/pytest` verbatim, confirmed by the E9 experiment) could resolve outside `repo_root` by accident and mark an in-tree shadow binary as trusted. `wrap_command_for_resolution(command, rc_path=None)` now writes the trailer to `rc_path` (two bare lines: resolved bin, exit code) instead of stdout when a path is given; `rc_path=None` keeps the old stdout-marker form for harnesses that don't preserve env across the whole compound command. New `parsers.read_rc_file(path)` parses that file (pure read; does not unlink). **hooks.py side still needed (Oliver)**: checked the official hooks reference directly (2026-09-19) -- it does *not* document whether `PostToolUse`'s `tool_input` reflects `PreToolUse`'s `updatedInput` rewrite or the original proposed command, so `on_post_tool_use` cannot safely re-derive the random `rc_path` by re-parsing `tool_input.command`. Both events do document `tool_use_id` for the same call, so the plan is a small pending-map keyed on it: `on_pre_tool_use` generates a fresh random path under `~/.receipts/rc/` (e.g. `secrets.token_hex`, not derived from `tool_use_id` or anything else model-visible -- the model already knows its own `tool_use_id`, so a path derived from it would defeat the "can't write to a path it hasn't seen" property), records `{tool_use_id: rc_path}` in a small per-session file (mirroring the existing `state/<session_id>.json` pattern) alongside passing `rc_path` into `wrap_command_for_resolution`; `on_post_tool_use` looks up `payload["tool_use_id"]` in that map, calls `parsers.read_rc_file(rc_path)`, sets `exit_code`/`resolved_bin`, then unlinks both the rc file and the map entry. Stale entries from a call whose `PostToolUse` never fires (interrupted mid-run) are a minor cleanup gap, not a correctness one. |
| E9 | Claude Code stores no exit code for Bash. Summary parsing vs PreToolUse wrapping vs Tier 3 re-run? | Oliver | decided | All three, in that order. **VERIFY resolved by experiment (2026-09-19): the wrapped command's trailer IS visible to the model** — a live `pytest --version` under the hook returned `__RECEIPTS_BIN=…` and `__RECEIPTS_RC=0` in the agent's own tool output. So the stdout trailer must go: write it to a per-call file instead (issue #22). MECHANICS §2. |
| E10 | Subagent laundering rule: parent claim backed only by a subagent's prose is unwitnessed; backed by the child ledger's event is confirmed | Oliver | open | Designed in MECHANICS §1, untested. Needs a fixture. |
| E6 | Claim extraction: LLM vs regex? | Oliver | decided (provisional) | **Per-sentence classification leads; regex is the no-key fallback.** Measured on 204 gold sentences (eval/results/2026-09-19-extraction.md): regex R=0.14/P=0.60, whole-report LLM R=0.32/P=0.87, per-sentence gpt-5-mini R=0.95/P=0.57, per-sentence gpt-5.2 R=0.68/P=0.83. Labels are machine-made and unstable run to run, so this orders the options rather than measuring the product; human labels pending. |
| E7 | Judge backend default | Oliver | decided | OpenAI SDK default, Anthropic behind the same interface. |
| E8 | Hash chain: keep, given the model has no write path anyway? | Anush | open | Cheap; keep for tamper-evidence of the stored file, but do not oversell it. |
| E11 | Wire `compress.py`'s bear-2 pre-processor into `judge.py`'s prompt assembly: swap `render_window` for `Compressor.compress_window` per-backend or once before both? What happens to the judge call on a compression failure -- fall back to the uncompressed window, or abort? | Oliver | open | `compress.py` (Anush, #17) ships `Compressor.compress_window(window) -> str`, the same shape as `judge.render_window`, and `make_compressor()` returns `None` when off (config `[compress].enabled` plus `RECEIPTS_TTC_API_KEY`; key presence alone is not enough). Not wired into `judge.py` here on purpose -- that's a call on Oliver's prompt-assembly code, not made in this PR. |

## Adapters and integrations
| # | Question | Owner | Status | Notes |
|---|---|---|---|---|
| A1 | Codex rollout JSONL field mapping | Ananya | decided | Format observed on this machine (MECHANICS §2): session_meta, turn_context, function_call/exec_command, exec_command_end (argv), function_call_output with "Process exited with code N", patch_apply_end, agent_message, task_complete. exec_command_end has argv, exit_code, aggregated_output, duration; patch_apply_end has success and per-path changes; task_complete has last_agent_message. Full map in docs/ADAPTERS.md §3. |
| A2 | Devin: which evidence path first? | Ananya | open | See docs/DEVIN.md. Path C (PR + structured_output + CI + git) ships first; Path A (CLI hooks in `.devin/hooks.v1.json`, `--export` ATIF) for the live demo; Path B (blueprint-planted recorder) is the Cognition showpiece and has three VERIFYs. Booth hour 0: org API token, blueprint editing on hackathon plan, send-message endpoint. |
| A3 | Copilot session log format and stable link from a PR | Ananya | open | Documented as attached to commits; verify the export. |
| A4 | Cursor: SpecStory export vs hooks | Ananya | deferred (post-event) | |
| A6 | Codex loop: lifecycle hooks (config reference says they exist) vs `codex exec resume` with the nudge as next prompt | Ananya | open | VERIFY hook shape; until then class F with resume. docs/ADAPTERS.md §3. |
| A7 | Universal recorder (class M): DEBUG trap vs PATH-first shell wrapper inside agent-spawned shells | Oliver | open | docs/ADAPTERS.md §4. Also the Devin cloud recorder. |
| A5 | `transcript_path` lag in Claude Code hooks | Oliver | decided | Use `last_assistant_message` from Stop/SubagentStop for the report; transcript only for history. |

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
