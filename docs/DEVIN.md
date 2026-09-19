# Receipts for Devin

Devin is the primary target. It is the sponsor with the largest single prize at this event, it ships PRs whose descriptions nobody checks against the session, and its docs say Devin Review "analyzes diffs", not descriptions. Everything below is what Devin exposes as of Sept 2026 and how Receipts uses it. Items tagged `VERIFY` must be confirmed at the Cognition booth or on a real account before they are relied on.

## What Devin exposes

| Surface | What it gives us | Independence |
|---|---|---|
| **Devin CLI** (`.devin/hooks.v1.json` in the repo, or `~/.config/devin/config.json`; `devin --print` headless; `devin --export out.json` writes the session in ATIF trajectory format after each turn) | `PreToolUse` (can rewrite `tool_input` via `updatedInput`, exit 2 blocks), `PostToolUse` (`tool_name`, `tool_input`, `tool_response` with `success`, `output`, `error`), `Stop` (`stop_hook_active`; `{"decision":"block","reason":...}`), `SessionStart`/`SessionEnd`, `PostCompaction`. Matchers by tool name. Same shape as Claude Code hooks, and the config can live in the repo. | Harness-written. Model has no write path. |
| **Cloud Devin environment blueprints** | Shell steps at build time; variables written to `$ENVRC` are "exported and available to all subsequent steps and the Devin session"; secrets injected as env vars; file attachments land in `~/.files/`. | Lets us plant a recorder in the machine before Devin starts. |
| **Sessions API v3** | Create session (`prompt`, playbook, tags, secrets, knowledge); get session (`status`, `pull_requests`, `structured_output`, `url`, ACUs); list messages (`source ∈ {devin, user}`); send a message to a running session `VERIFY exact path`. | Report and chat log. No tool calls. |
| **PRs, commits, CI** | Devin pushes branches and opens PRs; every commit is in git; CI logs are on GitHub. | Ground truth for edit/create/commit/test claims. |
| **Shell tool UI** | "View every command Devin has executed during the session" with output; copy per command; no batch export. Session Insights and the Issue Timeline are UI features `VERIFY API`. | Human-visible only. |
| **Dynamic Workflows** | Python orchestration on Devin's infra: `agent(prompt, schema)` returns structured output; `pipeline`, `parallel`; resumable by prompt hash. | Fan-out for the bench. |
| **Knowledge, Playbooks, Skills (SKILL.md)** | Org and repo instructions Devin follows. | Cooperative only; never evidence. |

## Three evidence paths, strongest first

### Path A: Devin CLI, hooks in the repo (identical to the Claude Code path)
Ship `.devin/hooks.v1.json` with the repo. `PostToolUse` writes CALL and RESULT events (the `success` flag is a real outcome signal, better than Claude Code's `is_error`); `PreToolUse` optionally wraps known runners to surface exit codes; `Stop` runs the ladder and, in auto mode, returns `{"decision":"block","reason":<nudge>}`. Nudges are templates. This is the fastest way to a live Devin demo, and any repo that adopts Receipts gets it just by committing the file.

The final message: the `Stop` payload table shows no `last_assistant_message`, so run every CLI session with `--export <file>` (ATIF, written after each turn) and read the last assistant turn from there. `devin --print` is the headless mode the bench runner uses; pass `--respect-workspace-trust false` in scripts.

### Path B: Cloud Devin, recorder planted by the blueprint
Devin cloud has no hooks, but the machine is ours before Devin arrives.
1. **Initialize step** installs `receipts-recorder`: a shell hook (`BASH_ENV` pointing at a script that sets a `DEBUG`/`ERR` trap and `PROMPT_COMMAND`, plus the same in `.bashrc`/`.zshrc`) that appends every executed command, its cwd, start/end time, and exit status to `~/.receipts/ledger.jsonl`, hash-chained. `VERIFY`: that Devin's Shell tool runs through a bash/zsh that honors these; if it runs `sh -c`, use a `PATH`-first wrapper for `sh`/`bash` that execs the real shell with the trap loaded.
2. **`$ENVRC`** exports `RECEIPTS_INGEST_URL` and, from Secrets, `RECEIPTS_TOKEN`; the recorder also POSTs each event to our ingest endpoint so the ledger survives the VM. If Devin restricts egress under a security profile, the ledger is committed to the branch as `.receipts/ledger.jsonl` by a post-task step Devin is told to run (cooperative fallback, flagged as such).
3. **Session correlation.** No `DEVIN_SESSION_ID` env var is documented. Correlate by git remote + branch: the recorder logs `git rev-parse --abbrev-ref HEAD` and the remote URL with every event; the Devin API's `pull_requests` for a session names the branch. That join is deterministic.
4. **The report** is the PR description plus, when we ask for it in the prompt, `structured_output` with a claims list (self-report; a claim source, never evidence) plus the last `devin` message from the messages API.
5. **Evidence** is the recorder ledger (commands, exit codes), git (diffs, SHAs), CI logs (runner output), and the filesystem at PR time via a checkout.

Independence note: the recorder is harness-independent in the sense that Devin does not write it and cannot easily notice it, but it is inside the VM Devin controls. A determined agent could `unset BASH_ENV`. Tier 2 catches the absence (a test claim with no recorded runner becomes `unwitnessed`) and the CI re-run (Tier 3) is the backstop. State this plainly on the slide; Cognition's engineers will ask.

### Path C: Devin API and GitHub only (no recorder)
Report = PR description + `structured_output` + last message. Evidence = git + CI + checkout. Settles edit, create, delete, commit, and test claims when CI ran; everything else is `unwitnessed`. This is the day-zero adapter and works on any Devin PR with zero setup. Ship it first.

## The correction loop for Devin
- **CLI:** the Stop hook, exactly as for Claude Code.
- **Cloud:** after the receipt is computed on a PR, send the nudge as a message to the still-running session through the Sessions API (`VERIFY path`), or, if the session has ended, comment on the PR with the nudge and let a Devin GitHub automation pick it up (Automations trigger sessions from GitHub events). Nudges are the same deterministic templates. A retry clears a mark only when new recorder events, a new commit, or a new CI run bear on the claim.
- **Cap:** three passes, then the PR gets the `needs-receipt` label and a human.

## What to show Cognition
1. **Devin built a subsystem unattended** (bench fixtures and oracles, or the GitHub Action), a dozen Devin PRs in the history.
2. **The recorder in a blueprint**: a Devin session on a trap repo; the receipt on its PR shows `pytest | tail -5` caught by the recorder's exit-status log, then the nudge sent through the API, then the corrected PR. That is "Receipts checks Devin, and talks back to Devin, using Devin's own surfaces."
3. **Path C on someone else's Devin PR** (any public repo with a Devin-authored PR): the receipt with zero setup.
4. **The honest line:** "Devin Review reads the diff. Receipts reads the description against the session. Both are needed."

## Bench on Devin
Dynamic Workflows fan out: `agent(trap_prompt, schema=ReportSchema)` per trap × N; each session opens a PR on a fixture branch; Receipts scores each PR with Path B or C; the per-trap false-report rate for Devin goes on the same chart as Claude Code and Codex. Devin's structured-output schema asks for `{claims: [...], tests_run: bool, commands: [...]}`; the gap between that self-report and the recorder is itself a measurement (self-report fidelity, the AER paper's unimplemented metric).

## Owners and hours
- Ananya: Path C adapter (2 h), blueprint recorder (3 h incl. `VERIFY`s), API nudge (1 h).
- Anush: recorder's hash chain and the CI-log runner parsers (shared with Path A).
- Oliver: the Devin demo script and the slide.
- Booth, hour 0: credits; whether hackathon accounts get an org API token; whether blueprints are editable on that plan; confirm the send-message endpoint.

## VERIFY list
1. Devin CLI: ATIF export field names for tool calls and outputs (write the golden test from one real export); confirm `Stop` has no final-message field.
2. Sessions API: exact send-message path and body; whether messages are accepted while a session is running.
3. Blueprints: that `BASH_ENV`/`.bashrc` changes from the Initialize step persist into the session shell and that Devin's Shell tool runs through bash.
4. Any session id available inside the machine (env var or file), which would replace the branch join.
5. Session Insights: the generate endpoint exists (`post-organizations-session-insights-generate`); the docs describe an Issue Timeline and categories, not a command list. Check whether the response carries timeline events with commands; if so it is a Path B substitute.
