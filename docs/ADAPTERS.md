# Adapters: Claude Code and Codex now, Devin when we have access, any agent by construction

The engine never sees an agent. It sees a **report** (text) and a **ledger** (events). Adapters produce both. This doc specifies the Claude Code and Codex adapters concretely enough to build, and then shows why every coding agent falls into one of four evidence classes, so coverage is a property of the architecture rather than a list of integrations.

## 1. The four evidence classes (why "no matter the agent" holds)

| Class | How the ledger is obtained | Live loop? | Agents |
|---|---|---|---|
| **H. Hooks** | The harness calls us on every tool call and at turn end; we write the ledger and can block the stop. | Yes | Claude Code, Devin CLI, Cursor (hooks; Stop cannot block), Codex `VERIFY` (config mentions lifecycle hooks) |
| **F. File** | The harness writes a session file we parse after the fact or tail live. | Via resume: the nudge is the next prompt to the same session | Codex (rollout JSONL), Claude Code (transcript JSONL), Devin CLI (`--export` ATIF), Cursor via SpecStory |
| **R. Remote** | The agent runs elsewhere and produces a PR. Evidence is git, CI logs, a checkout, plus a recorder planted in the remote machine when the machine is configurable. | Via the agent's API (message a running session) or a PR comment that an automation picks up | Devin cloud, Copilot coding agent, Codex cloud, any PR bot |
| **M. Machine** | Nothing from the harness. A recorder on the developer's machine logs every shell command with exit status, every file change, and every git ref move, independent of which agent caused it. | No block; post-hoc receipt and next-prompt nudge | Anything: a chat window, an IDE agent with no hooks, an agent we have never heard of |

Every agent is in at least one class. Class M is the floor: a shell trap (`DEBUG`/`ERR` traps, `PROMPT_COMMAND`, or a `PATH`-first `bash`/`sh` wrapper), a filesystem watcher on the workspace (`fswatch`/`inotify`, debounced to the shell events), and `git reflog`. It records what the machine did whether or not the agent cooperates. The report side is always available: it is text in some UI, a PR description, or a paste. So the weakest guarantee we make for an unknown agent is: **post-hoc receipt from machine evidence, no blocking, nudge delivered as the next prompt.** Every stronger class adds blocking, exact tool attribution, or both.

What changes between classes is only *which claims are settleable*: class M cannot attribute a command to an agent turn precisely (it uses timestamps), cannot see tool calls that never touch the shell (an in-process file write by an IDE agent shows up only as a filesystem event), and cannot block. Everything in the claim table in `docs/MECHANICS.md` §3 still applies; the verdicts just skew toward `unwitnessed` and `unrecorded` where attribution is missing, which is the honest answer.

## 2. Claude Code adapter (class H live, class F post-hoc)

### Live (hooks)
Config the user installs with `receipts watch` (or ships in `.claude/settings.json` for a repo):

```json
{"hooks": {
  "PreToolUse":  [{"matcher": "Bash", "hooks": [{"type": "command", "command": "receipts _hook pre",  "timeout": 5}]}],
  "PostToolUse": [{"matcher": "",     "hooks": [{"type": "command", "command": "receipts _hook post", "timeout": 10}]}],
  "Stop":        [{"matcher": "",     "hooks": [{"type": "command", "command": "receipts _hook stop", "timeout": 120}]}],
  "SubagentStop":[{"matcher": "",     "hooks": [{"type": "command", "command": "receipts _hook subagent-stop"}]}]
}}
```

| Hook payload field | Ledger field | Notes |
|---|---|---|
| `session_id` | `session_id` | key `claude_code:<session_id>` |
| `cwd` | `cwd` | resolve relative paths against it |
| `agent_id`, `agent_type` | `flags.sidechain=true`, child ledger id | present only inside subagents |
| `tool_use_id` | join key between pre/post | |
| `tool_name` | `tool` | `Bash`, `Edit`, `Write`, `MultiEdit`, `Read`, `Glob`, `Grep`, MCP tools (`mcp__*`) |
| `tool_input.command` (Bash) | `input.command`, `paths` from tokens that exist on disk | pipe flags computed here |
| `tool_input.file_path` (Edit/Write/Read) | `paths[0]` | |
| `tool_response` (Post) | `output` (≤4 KB) + `output_hash`; for Bash, stdout and stderr arrive as one string | no exit code: see below |
| `last_assistant_message` (Stop, SubagentStop) | TEXT event = the report | never read the transcript for the current turn |
| `stop_hook_active` | loop state | true means we are inside our own continuation |

**Exit code.** Not provided. Order of resolution: (1) parse the runner summary from `tool_response` (`12 passed`, `1 failed`, `collected 0 items`, `FAIL`, `error TS`); (2) for commands whose first token resolves to a known runner or build tool, the `PreToolUse` hook returns `hookSpecificOutput.updatedInput.command` = `(<original>); __rc=$?; printf '\n__RECEIPTS_RC=%s\n' "$__rc"; exit $__rc` and the Post hook strips the trailer from what it stores and records `exit_code`; `VERIFY` whether the model sees the trailer, and if it does, keep the wrapper only for runners and document it; (3) Tier 3 re-run. Never wrap commands containing heredocs, `&`, or `nohup`.

**Stop hook contract.** Exit 0 with no output when clean (or manual mode). In auto mode with open claims: print `{"hookSpecificOutput":{"hookEventName":"Stop","decision":"block","reason":"<nudge text>"}}` and exit 0. The nudge is built from templates. State lives in `~/.receipts/sessions/<id>.sqlite` (`passes`, open claim ids, nudge seq). Rules run synchronously; Tier 3 and Tier 4 run in a detached process and post a follow-up receipt via the extension or `receipts check`.

### Post-hoc (transcript)
File: `~/.claude/projects/<escaped-cwd>/<session_id>.jsonl`. Keep only records with `type ∈ {assistant, user}` and a `message.content` list.

| Transcript | Ledger |
|---|---|
| assistant `tool_use` block `{id, name, input}` | CALL, `seq` by file order, `ts` from record `timestamp` |
| user `tool_result` block `{tool_use_id, content, is_error}` + record `toolUseResult{stdout, stderr, interrupted}` | RESULT; `exit_code` unknown; `is_error` → `flags.error`; `interrupted` → `flags.interrupted` |
| assistant `text` block on the main chain, last before the next user prompt | TEXT (report for that turn) |
| `isSidechain: true` | child ledger keyed by `parentUuid` chain |
| record `cwd`, `gitBranch`, `version` | session metadata |

Golden test: three real transcripts from this machine (redacted) → expected ledger JSONL.

## 3. Codex adapter (class F, fully specified from a real rollout)

File: `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<session_id>.jsonl`. Written by both Codex CLI and the desktop app (`session_meta.originator` = `Codex Desktop` in the sample). Tail it live or parse after.

| Rollout line (`type` / `payload.type`) | Ledger |
|---|---|
| `session_meta` `{id, cwd, originator, cli_version, timestamp}` | Session: key `codex:<id>` |
| `turn_context` `{turn_id, cwd, sandbox_policy, approval_policy}` | Turn boundary; `sandbox_policy` is evidence for what could not have happened |
| `response_item/function_call` `{name: "exec_command", arguments: json{cmd, workdir, max_output_tokens}, call_id}` | CALL, `tool="Bash"`, `input.command=cmd`, join on `call_id` |
| `event_msg/exec_command_end` `{call_id, command: [argv...], cwd, exit_code, aggregated_output, stdout, stderr, duration{secs,nanos}, status, parsed_cmd}` | RESULT with **exact argv**, `exit_code`, `duration_ms`; `parsed_cmd` tells us the tool class; `aggregated_output` is the full output before the model-facing truncation |
| `response_item/function_call_output` `{call_id, output}` where `output` begins `Command: … Process exited with code N … Original token count: T … Output:` | The model-facing view; use only to set `flags.truncated` when `Original token count` exceeds `max_output_tokens` |
| `response_item/custom_tool_call` `{name: "apply_patch", input: "*** Begin Patch …", call_id}` | CALL, `tool="Edit"`, paths parsed from `*** Add File:` / `*** Update File:` / `*** Delete File:` |
| `event_msg/patch_apply_end` `{call_id, success, changes: {path: {type: add|update|delete, content}}, stdout, stderr}` | RESULT for edit/create/delete with per-path change type; `success=false` → `flags.error` |
| `custom_tool_call_output` `{call_id, output: json{output, metadata{exit_code, duration_seconds}}}` | secondary exit code for patches |
| `event_msg/web_search_end`, `response_item/web_search_call` | CALL/RESULT `tool="WebSearch"` (evidence for "I checked the docs") |
| `event_msg/agent_message` `{message}` | intermediate assistant text (not the report) |
| `event_msg/task_complete` `{turn_id, last_agent_message}` | **TEXT = the report for the turn**; `turn_aborted` → no report |
| `compacted`, `thread_rolled_back` | integrity flags: history before this point may be summarized or discarded |
| `event_msg/token_count` | ignore, except to compute the cost slide's agent-side tokens |

Codex is the best-instrumented harness we have: argv, exit codes, full output, and structured patch results all exist. Tier 2 needs no wrapper trick here.

**Loop for Codex.** Still class F here (no code change in this pass), but the `VERIFY` is answered: the nudge is delivered by resuming the session non-interactively with the template as the next prompt -- confirmed exact shape by installing the real CLI (`npx @openai/codex@latest`, v0.155.1) and reading `codex exec resume --help`: `codex exec resume [SESSION_ID] [PROMPT]`, `--last` to pick the most recent session, `--json` for JSONL events, `-o/--output-last-message <FILE>` for the report text -- all exactly as guessed, no longer `VERIFY`. The bench runner uses `codex exec` headless with the rollout as the ledger, same as before.

**Codex hooks: real, stable, and close enough to Claude Code's shape to justify class H (VERIFY resolved 2026-09-19, Anush).** `codex features list` on the installed CLI shows `hooks  stable  true` -- not experimental, on by default -- and `codex exec --help` documents `--dangerously-bypass-hook-trust`, confirming a live hook-trust system exists, not just a config-reference mention. Per the official docs (developers.openai.com/codex/hooks, redirects to learn.chatgpt.com/docs/hooks) cross-checked against github.com/openai/codex/blob/main/docs/config.md (the `allow_managed_hooks_only` detail matches verbatim), Codex supports `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStop` -- the exact four events this project's own `hooks.py` implements -- plus `SessionStart`/`SessionEnd`, `PermissionRequest`, `PreCompact`/`PostCompact`, `UserPromptSubmit`, `SubagentStart`, `Interrupt`, which Claude Code doesn't have equivalents for. Field-level match on the ones we use:
- Shared payload keys `session_id`, `transcript_path`, `cwd` -- identical names to Claude Code's.
- `PreToolUse` payload: `tool_name`, `tool_use_id`, `tool_input` -- identical names; response can set `permissionDecision: "allow"` plus `updatedInput`, the same `updatedInput` key `hooks.on_pre_tool_use` already emits (nested under `hookSpecificOutput` for Claude Code; top-level for Codex per the docs -- the one real shape difference found).
- `PostToolUse`/`Stop`/`SubagentStop` can all block by returning `{"decision": "block"}` -- the *exact* JSON `hooks.on_stop` already returns today. Exit code 2 + stderr also blocks, matching Claude Code's Stop-hook convention too.

**Not verified by actually running a live hooked session** -- no Codex account/API key in this environment, so this is documentation-plus-CLI-introspection, not an experiment like E9's. The `updatedInput` nesting difference and the trust-review step (`/hooks` command, hash-pinned trust) both need a real session to confirm behaviorally before writing a `hooks.py`-equivalent for Codex. That build -- and the class F→H upgrade it would enable -- is real, valuable, and substantial enough that it's a call for whoever owns `codex.py` (Ananya) to make, not started here.

Sample claim to test the extractor on, from the real rollout on this machine: "Verified local Round 2 result is now `297,604`, up from `297,387`." That is an `observed_output` claim; the evidence is an `exec_command_end` whose `aggregated_output` contains `297,604`.

## 4. The universal recorder (class M), so the floor exists

`receipts record` installs, per shell:
- **bash:** `PROMPT_COMMAND` captures the previous command's exit status and `history 1`; a `DEBUG` trap captures the command text and start time. Written to `~/.receipts/machine/<host>-<date>.jsonl` with `cwd`, `tty`, `pid`, `ppid` (the ppid chain is how we attribute a command to an agent process when one is running).
- **zsh:** `preexec`/`precmd` hooks, same fields.
- **non-interactive shells:** a `PATH`-first `bash` and `sh` wrapper that logs argv and exit status and `exec`s the real shell.
- **filesystem:** `fswatch` on the workspace, events debounced and joined to the nearest shell event by time.
- **git:** `reflog` diffs at each Stop or `check`.

Attribution: when a class-H or class-F agent is also present, its events win; machine events are used only to fill `exit_code` gaps (Claude Code) or to confirm state. When no harness is present, machine events are the ledger and claims that need tool attribution are marked `unwitnessed` rather than confirmed on timing alone.

This is also the Devin cloud recorder (Path B in `docs/DEVIN.md`), installed by the blueprint instead of by the user.

## 5. Cursor, Copilot, and anything else

- **Cursor:** hooks exist (`stop` cannot block); SpecStory writes session files. Class H for logging, class F for the report, class M for exit codes. Loop: next prompt.
- **Copilot coding agent:** class R; session logs are linked from commits; report is the PR description. Loop: PR comment that the agent picks up.
- **Codex cloud:** class R; report is the PR description; evidence is git and CI.
- **Unknown IDE agent:** class M plus the report pasted or read from the PR.

## 6. Build order

1. Claude Code post-hoc adapter and golden tests (this machine has 779 transcripts). Enables `receipts check --last`.
2. Codex post-hoc adapter and golden tests (this machine has rollouts). Enables the same for Codex Desktop users with no install.
3. Claude Code live hooks and the Stop loop. Enables the demo and auto mode.
4. Class M recorder. Enables exit codes for Claude Code and the "any agent" claim.
5. Devin Path C, then A and B when access arrives.
6. Codex hooks or resume-based loop after `VERIFY`.

## 6a. Status (what exists in `src/receipts/adapters/`)

| Adapter | Class | State | Tests |
|---|---|---|---|
| `claude_code.py` | H / F | shipped | `tests/golden/claude_code/` |
| `codex.py` | F | shipped: rollout JSONL, turn boundaries, shell pairing, exact exit code, pipe and truncation flags, patch paths, aborted turns, compaction and rollback | `tests/golden/codex/` |
| `machine.py` | M | shipped: bash/zsh snippets (`receipts record --install`), the `_record-line` wire writer, the JSONL parser. **Not shipped:** the `fswatch` watcher and the `reflog` poller of §4 (the parser reads `fs`/`git` rows, nothing emits them yet), and the class-H exit-code join of §4 — borrowing a status from an appendable, unattributed log into a chained harness ledger needs a per-row provenance marker and a re-chain first | `tests/unit/test_machine_recorder.py` |
| `devin.py` | R | shipped (Path C): bundle parse, `fetch_bundle` (GET `/v1/sessions/{id}`), `nudge` (POST `/v1/sessions/{id}/message`). Paths A and B wait on access | `tests/golden/devin/` |
| `copilot.py` | R | shipped: PR body report, optional tool log, commits and checks. Log export format still A3 | `tests/golden/copilot/` |
| `otel.py` | trace | shipped: OTLP JSON and JSONL, GenAI spans, `execute_tool` call/result pairing, status and `process.exit_code` mapping. Uncaptured tool results are flagged, never assumed | `tests/golden/otel/` |

Every adapter is `parse(path) -> (Session, list[LedgerEvent], report | None)` and is registered in
`adapters/__init__.py`; `receipts check <file>` sniffs the format, `--agent` overrides it.

Class R and any span with no captured result write a `no_tool_log` / `stderr_dropped` marker, and
`verdicts.run` turns the affected `unwitnessed` verdicts into `unrecorded`: a record that admits its
own gap must not read as silence.

## 6b. The receipt on the PR (product sketch B)

`.github/workflows/receipt.yml` runs `receipts pr-comment` on every PR event and keeps exactly one
comment, found by the marker `<!-- receipts-bot: pr-receipt -->`. Evidence, first match wins: a
`receipts-session` artifact from a **successful run of this repo's own session workflow** on the
head SHA (`vars.RECEIPTS_SESSION_WORKFLOW`, default `ci`), else a class-R bundle built from the PR,
its commits, its files and its check runs by `.github/workflows/pr_bundle.jq` (tested in
`tests/unit/test_pr_bundle_jq.py`).

A log committed to the PR branch is **not** a source. It has no provenance, so an agent could
commit a ledger that confirms its own report — invariant 1, the one failure this tool exists to
catch. Restoring it needs a harness signature, or a verdict path that marks unverified-provenance
evidence as such instead of scoring it like a harness-written ledger.

It checks out the base ref under `pull_request_target`, never runs PR code, and passes every
untrusted value (paths, URLs, PR text) through `env:` rather than `${{ }}` inside a shell line.
The deterministic tiers are the default: the judge runs only where a repo sets
`vars.RECEIPTS_JUDGE` and a key exists. Invariant 9 is intact: this is the repo's own CI on its own
PRs.

## 7. VERIFY
- Claude Code: is a `PreToolUse`-rewritten command visible to the model?
- ~~Codex: lifecycle hook shape and config; `codex exec` flags for JSON output, last-message file, and resume.~~ **Resolved (2026-09-19, A6):** see §3 -- hooks are real, stable, and closely match Claude Code's shape (same event names and block/`updatedInput` mechanism for the four we use); `codex exec`/`exec resume` flags confirmed against the installed CLI's own `--help`. Not yet run against a live hooked session (no account here) -- that behavioral confirmation, and the class F→H rebuild it would justify, is still open, Ananya's file.
- Cursor: hook payload fields and SpecStory file format.
- Class M: `DEBUG` trap behaviour inside the shells Claude Code and Codex spawn (they run `bash -c`/`zsh -lc`; the wrapper approach covers this if traps do not).
