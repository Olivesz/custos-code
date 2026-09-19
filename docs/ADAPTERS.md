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

**Loop for Codex.** No hook we can rely on yet (`VERIFY`: the config reference mentions "user, project, and session hook configs", so lifecycle hooks exist in some form; if they match Claude Code's shape, Codex moves to class H). Until then: the nudge is delivered by resuming the session non-interactively with the template as the next prompt (`codex exec resume <id> "<nudge>"`, flags `VERIFY`), and the bench runner uses `codex exec` headless with the rollout as the ledger. Blocking is not possible in class F; auto mode for Codex is "hold the report in our UI, resume with the nudge, show the clean report."

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
| `machine.py` | M | shipped: bash/zsh snippets (`receipts record --install`), the `_record-line` wire writer, the JSONL parser, and `merge_exit_codes` for filling a class-H gap. **Not shipped:** the `fswatch` watcher and the `reflog` poller of §4 — the parser reads `fs`/`git` rows, nothing emits them yet | `tests/unit/test_machine_recorder.py` |
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
`receipts-session` artifact on the head SHA, a committed `.receipts/session.jsonl`, or a class-R
bundle built from the PR, its commits, its files and its check runs by `.github/pr_bundle.jq`
(tested in `tests/unit/test_pr_bundle_jq.py`). It checks out the base ref under
`pull_request_target` and never runs PR code; without `OPENAI_API_KEY` it falls back to
`--rules-only`. Invariant 9 is intact: this is the repo's own CI on its own PRs.

## 7. VERIFY
- Claude Code: is a `PreToolUse`-rewritten command visible to the model?
- Codex: lifecycle hook shape and config; `codex exec` flags for JSON output, last-message file, and resume.
- Cursor: hook payload fields and SpecStory file format.
- Class M: `DEBUG` trap behaviour inside the shells Claude Code and Codex spawn (they run `bash -c`/`zsh -lc`; the wrapper approach covers this if traps do not).
