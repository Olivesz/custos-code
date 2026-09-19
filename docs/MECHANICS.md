# Mechanics: how the receipt gets made

The questions that decide whether this ships: which chat is which, what gets logged and by whom, what "the report" is, which claims can be checked by what, and how the loop knows a retry did anything. Everything below is grounded in observed formats (Claude Code transcripts on this machine, Codex rollout files on this machine, the Claude Code hooks reference as of Sept 2026). Unverified items are tagged `VERIFY`.

## 1. Identity: which chat, which turn, which report

### Sessions
- **Claude Code.** Every hook payload carries `session_id`, `transcript_path`, `cwd`. Every transcript record carries `sessionId`, `cwd`, `gitBranch`, `uuid`, `parentUuid`, `timestamp`, `version`. One transcript file per session at `~/.claude/projects/<escaped-cwd>/<session_id>.jsonl`. Resumed sessions keep the id. Two windows in the same repo are two `session_id`s; never key on `cwd`.
- **Codex.** One rollout file per session at `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<session_id>.jsonl`. First line is `session_meta` with `payload.id`, `payload.cwd`, `originator` (`Codex Desktop`, CLI), `cli_version`. Codex has no hooks; the adapter tails the file.
- **Copilot coding agent / Devin.** Session is identified by the PR (Copilot links session logs from each commit; Devin lists `pull_requests` per session). Key on PR number plus the session link.

**Receipts key:** `source:session_id`. The ledger is one SQLite file per key. Subagent work is a child ledger (below) linked to the parent.

### Turns and "the report"
A receipt is per **turn**, not per session: the unit is one assistant final message. What counts as final:
- **Claude Code.** The `Stop` hook fires when Claude finishes a turn and carries `last_assistant_message` (the full text). Use that; the transcript is written asynchronously and can lag the turn, which the docs say explicitly. `stop_hook_active: true` means we are already inside a stop-hook continuation; that flag is how the loop counts passes and avoids recursion.
- **Codex.** `event_msg/task_complete` closes a turn; the last `response_item/message` with `role: assistant` (or the last `event_msg/agent_message`) before it is the report. `turn_context.turn_id` groups everything in the turn. `turn_aborted` means no report.
- **Copilot/Devin.** The PR description is the report. Devin's `structured_output` is a second, machine-readable report when present.

Claims in one report may refer to work from earlier turns ("as I mentioned, I already added the tests"). The evidence window for a claim is the whole session ledger, but recency matters: the matcher prefers events from the current turn and falls back to earlier turns, and the verdict records which turn the evidence came from.

### Subagents
- **Claude Code.** Hooks fire inside subagents too; the payload carries `agent_id` and `agent_type`. Transcript records of subagent turns have `isSidechain: true`. `SubagentStop` delivers the subagent's `last_assistant_message`. Rule: a subagent's *report* is a claim source for the parent (the parent often repeats it), but a subagent's *tool calls* are evidence only for claims about what the subagent did. A parent saying "the tests pass" backed only by a subagent's prose is `unwitnessed`; backed by the subagent's actual `pytest` event it is `confirmed` with the child ledger cited. This is the multi-agent laundering case from the research (a critic approving a false narrative).
- **Codex.** Sub-tasks appear in the same rollout; `VERIFY` whether delegated runs get separate files.

## 2. Logging: who writes what, and what is missing

### Claude Code, live path (hooks)
| Event | Fields we use | What we write |
|---|---|---|
| `PreToolUse` | `tool_name`, `tool_input`, `tool_use_id` | Reserve a ledger seq; for Bash, optionally rewrite the command (see exit codes) via `hookSpecificOutput.updatedInput` |
| `PostToolUse` | `tool_name`, `tool_input`, `tool_use_id`, `tool_response` (for Bash: stdout/stderr as one string) | The CALL and RESULT events, hashed, with paths extracted from `tool_input` (`file_path`, `path`, `command`) |
| `Stop` | `last_assistant_message`, `stop_hook_active`, `session_id` | The TEXT event (the report), then run the ladder; in auto mode decide block/continue |
| `SubagentStop` | `agent_id`, `agent_type`, `last_assistant_message` | Child report event linked to the parent |

Default hook timeout is 600 s for commands, so the Stop hook has room, but the developer is waiting; rules run synchronously, re-runs and the judge run async and post a follow-up.

### Claude Code, post-hoc path (transcript)
`type: "assistant"` records hold `message.content[]` blocks of `text` and `tool_use` (`id`, `name`, `input`). `type: "user"` records hold `tool_result` blocks (`tool_use_id`, `content`, `is_error`) plus a top-level `toolUseResult` object. For Bash, `toolUseResult` has `stdout`, `stderr`, `interrupted`, `isImage`, `noOutputExpected`. **It has no exit code.** `is_error` is the only success signal and it is coarse. Records with `isSidechain: true` are subagent turns. Many other record types exist (`attachment`, `queue-operation`, `file-history-snapshot`, ...) and are ignored.

### Codex (rollout file, post-hoc or tailed)
| Line | Payload fields | Maps to |
|---|---|---|
| `session_meta` | `id`, `cwd`, `originator`, `cli_version` | Session |
| `turn_context` | `turn_id`, `cwd`, `sandbox_policy`, `approval_policy` | Turn boundary; sandbox facts are evidence for "could not have touched X" |
| `response_item/function_call` | `name` (`exec_command`, `apply_patch` via `custom_tool_call`), `arguments` (JSON: `cmd`, `workdir`), `call_id` | CALL |
| `event_msg/exec_command_end` | `call_id`, `command` (argv array), `cwd`, `process_id`, `turn_id`, exit status `VERIFY field name` | Ground truth for what binary actually ran (argv, not a string) |
| `response_item/function_call_output` | `call_id`, `output` text beginning `Command: ... Process exited with code N ... Output:` | RESULT with exit code parsed from the header |
| `event_msg/patch_apply_end` | patch summary `VERIFY fields` | Edit/create/delete events with paths |
| `event_msg/agent_message`, `response_item/message` (assistant) | text | The report (last before `task_complete`) |
| `event_msg/task_complete`, `turn_aborted` | `turn_id` | Turn close / no report |

Codex output is truncated by `max_output_tokens` on the call (default 2000 in the sample) and says so ("Original token count"), which is a first-class `truncated` flag.

### The exit-code problem (Claude Code) and the three ways to solve it
1. **Infer from `is_error`.** Free, coarse, already there. Good enough for Tier 1 ("did a runner get invoked") and for obvious failures. Not enough for "all passing".
2. **Parse the runner's own summary** from stdout (`12 passed`, `1 failed`, `collected 0 items`, `FAIL src/x.test.ts`). Deterministic, per-runner parsers, and it is what settles most test claims. Pipes that cut the summary make the claim `unrecorded`.
3. **Wrap the command in `PreToolUse`.** `updatedInput.command = "(<cmd>); __rc=$?; echo \"__RECEIPTS_RC=$__rc\"; exit $__rc"` for commands that match a known runner or build tool only. The exit code then appears in `tool_response` and the transcript. `VERIFY`: whether the rewritten command is what the model sees in its own context (if so, restrict wrapping to a hidden trailer and strip it from what we show). Do not wrap arbitrary commands; semantics of `&&` chains, backgrounding, and heredocs make general rewriting unsafe. Preferred order: 2, then 3 for runners, then Tier 3 re-run when both fail. The same trailer also carries `__RECEIPTS_BIN=$(command -v <argv0>)` (E5), so the wrapper-shadowing rule sees the binary the shell actually resolved, not the string the agent typed; `parsers.wrap_command_for_resolution`/`strip_and_parse_trailer` implement the wrap and the strip. `VERIFY(E5)`: whether that `command -v` reflects the agent's actual `PATH` if it mutated it mid-session.

### Completeness flags, set at ingest
- `truncated`: Claude Code caps tool output shown to the model; the transcript keeps what the model saw. Codex states the original token count. Either way, keep the full text when the hook has it and a sha256 of it always.
- `piped`: command contains `| head`, `| tail`, `| grep`, `2>/dev/null`, `> file`, `--silent`, `-q` beyond the runner's own quiet mode. Flag, do not judge.
- `interrupted`: Claude Code `toolUseResult.interrupted`, Codex `turn_aborted`.
- `sidechain`: subagent event.
- `unrecorded_tool`: an MCP or custom tool whose output the adapter does not understand. Counted, never used as evidence.

## 3. What can be verified by what

The claim type decides the evidence, the tier, and the failure verdict. This is the mapping the extractor and the rules share.

| Claim type | Example phrasing | Evidence that confirms | Tier | Turns into `contradicted` when | Otherwise |
|---|---|---|---|---|---|
| `run_tests` | "ran the suite, all passing", "12/12 green" | Bash/exec whose argv[0] resolves to a known runner (pytest, jest, vitest, go, cargo, gradle, xcodebuild, mvn, bun, npm/yarn/pnpm `test`) **and** parsed summary with 0 failures **and** exit 0 (or `is_error` false); Tier 3 re-run agrees | 2 (3) | Runner ran and failed; summary shows failures or 0 collected; re-run fails | No runner call: `unwitnessed`. Piped/truncated: `unrecorded`. Test files changed since task start: `qualified` |
| `build` | "builds clean", "compiles" | Known build tool (`npm run build`, `cargo build`, `go build`, `tsc`, `xcodebuild`) with exit 0 | 2 (3) | Non-zero exit / error lines | As above |
| `lint`/`typecheck` | "lint is clean", "mypy passes" | Known linter with exit 0 and summary | 2 (3) | Non-zero / findings printed | As above |
| `edit` | "updated auth/middleware.py", "changed the config" | Edit/Write/MultiEdit (Claude Code) or `apply_patch` (Codex) on a path that matches **and** the file differs from task start (git diff or mtime/hash) | 1 | Path never touched **and** git diff shows no change to it | Touched but path ambiguous: `unwitnessed` with candidates listed |
| `create` | "added tests/test_x.py" | Write/`apply_patch` add on path **and** file exists now | 1 | File does not exist | Exists but no write event (pre-existing): `qualified` ("already existed") |
| `delete` | "removed the old helper" | `rm`/`git rm`/patch delete **and** file absent | 1 | File still present | |
| `read`/`review_all` | "reviewed all 14 files", "read the docs" | Read events (or `cat`/`sed -n`/`head`) whose path set ⊇ claimed set; for "all", the set is enumerated from the claim's scope (directory listing at that time) | 1 | Claimed set minus read set is non-empty **and** the claim says "all" | Partial: `qualified` ("9 of 14 opened") |
| `run_cmd` | "ran the migration", "restarted the server" | Bash/exec whose command matches (normalized argv) with exit 0 | 2 | Matching command exited non-zero | No matching command: `unwitnessed` |
| `commit` | "committed as abc123", "pushed to main" | `git commit` event with SHA in output; `git log` / `git rev-parse` state check; for push, `git push` output naming the ref | 1/2 | SHA absent from `git log`; push output shows rejection | |
| `observed_output` | "the endpoint returned 200", "curl shows the header" | A RESULT whose text contains the quoted observation (normalized whitespace), from a command that could produce it | 2 | The RESULT shows the opposite (e.g. 500) | No such command: `unwitnessed` |
| `verify` | "verified end-to-end", "checked in the browser" | Only if it decomposes into one of the above (a curl, a test run, a script). The extractor asks: what tool call would this have produced? | 2/4 | The decomposed check ran and failed | Not decomposable (manual browser): `unwitnessed`, never contradicted |
| `did_not_touch` | "I did not modify the tests" | No edit event on the path set **and** git diff clean for it | 1 | Edit event or diff on the path | |
| `deploy` | "deployed to staging" | The deploy command with exit 0 plus, where available, a URL check RESULT | 2 | Command failed | `unwitnessed` |
| `design_property` | "made it idempotent" | A probe from the library run in Tier 3 (call twice, compare state) | 3/5 | Probe fails | No probe: `needs human`, shown, never confirmed |
| `state_assertion` | "the README already documents X" | Repo state check (grep) at Stop time | 1 | Grep finds nothing | |

Rules that apply to the whole table:
- **Positive evidence only for `contradicted`** (a failed exit, a missing file, a diff that shows the opposite). Absence is `unwitnessed`.
- **Two-evidence for anything that blocks:** transcript event plus current state (filesystem, git, or re-run).
- **Argv, not strings.** Codex gives argv; for Claude Code the rule resolves the first token of the command through PATH at check time and rejects binaries inside the repo tree (a `./pytest` wrapper) unless they are the repo's documented runner.
- **Time ordering.** Evidence must precede the report in the same session, and for `edit`/`create` the state check happens at Stop time, so a write-then-revert shows as `qualified` ("written at #14, reverted at #30").

## 4. Paths and commands: normalization
- Resolve every path in `tool_input` against the record's `cwd`; store both raw and resolved.
- Match claims to paths by: exact resolved path, then basename plus parent directory, then basename alone with `unwitnessed` and candidates listed. Never confirm on basename alone.
- Commands: strip leading `cd x &&`, env assignments, `uv run`, `npx`, `poetry run`, `bunx`; the runner is the first remaining token. `npm test`/`yarn test`/`pnpm test` resolve to the script in `package.json` at check time (the agent can edit that script; the rule reads the committed version from git, not the working tree, when they differ, and marks the claim `qualified` if they do).

## 5. The loop: how a retry clears a mark
- State per `session_id`: `passes`, the set of open claim ids, and the ledger seq at which each nudge was issued.
- On the next `Stop` with `stop_hook_active: true`, re-run the ladder on the new `last_assistant_message`. A previously open claim clears only if `confirmed` **and** at least one cited event has `seq >` the nudge seq **and** matches the claim's objects (path or argv). Confirmed on old evidence alone does not clear it (it was not confirmed before, so this cannot happen except through extraction drift; treat as still open).
- A withdrawn claim clears when the new report has no claim of the same type over the same objects.
- Nudge text is built from `templates/<verdict>.txt` with `{claim}`, `{seq}`, `{command}`, `{path}` substitutions. No model call.
- `passes >= max_passes` ends the loop; the report is shown with marks and the pass history.
- The blocking message reaches Claude via the Stop hook's `reason`; `systemMessage` on most events is shown **to Claude as context, not to the user**, so the user-facing overlay is the extension or the terminal renderer, not `systemMessage`.

## 6. The extension: which chat is which, on screen
- The extension watches `~/.receipts/sessions/`. Each ledger carries `source`, `session_id`, `cwd`, `git_branch`, `started`, and the last report text. The VS Code window's workspace folder selects candidate sessions by `cwd`; the newest `Stop` among them is "the chat on the right". With two agent panels open in one workspace, the view shows a session switcher listing `session_id` short hashes, agent name, and the first user message.
- The extension mirrors `last_assistant_message` with marks; it cannot decorate the agent extension's webview. Editor decorations key on resolved paths from `edit`/`create` claims.
- Terminal users get the same via the Stop hook `reason` (auto mode) or `receipts check --last` (manual).

## 7. Known gaps, in priority order
1. Claude Code exit codes (§2). Decide between summary parsing, PreToolUse wrapping for runners, and re-run. Default: all three in that order.
2. Extraction on open-set claims (E6). Measured at hour 10.
3. Codex: `exec_command_end` exit field name and `patch_apply_end` fields. `VERIFY` on three real rollouts.
4. Subagent laundering: the rule in §1 is designed, not tested.
5. `verify` decomposition: the extractor must propose the tool call that would have produced the check; when it cannot, the claim is `unwitnessed` and auto mode asks for a recorded check or a withdrawal.
6. E4's async delivery: `rerun.spawn_async` launches the Tier 3 worker as a detached `receipts _hook rerun-worker` subprocess (`start_new_session=True`) so the Stop hook can return without waiting on it. `VERIFY`: whether that child survives Claude Code tearing down the hook script's process group; unconfirmed, since nothing today invokes `_hook stop` end to end (blocked on claims/rules, see gap 2).
