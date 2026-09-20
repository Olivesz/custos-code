# Warp: what we can actually build against

Warp is a sponsor track (Best Developer Tool) and the terminal the demo runs in. The question this
doc settles is narrower than "does Warp work with custos-code": it is **where the evidence comes from
when the agent under test is Warp's own agent rather than Claude Code.** Everything below was read
off a live, logged-in Warp install on 2026-09-19 (Warp Stable, macOS 15.6). Facts are marked
`OBSERVED`; things I could not settle are marked `VERIFY` with the exact command that settles them.

Owner: Oliver. Adapters, if we ever build one, are Ananya's area (see `.github/CODEOWNERS`).

## 1. The three ways custos-code can meet Warp

| Tier | Mechanism | Build cost | Evidence quality | Verdict |
|---|---|---|---|---|
| **T0** | Claude Code (or Codex) runs as a CLI agent **inside a Warp pane**. Our existing hooks fire unchanged. | **Zero** | Full — this is our normal Claude Code ledger | **This is the demo.** |
| **T1** | Read Warp's own local SQLite (`blocks`, `commands`) as the ledger for **Warp Agent** | Small, ~1 adapter | Would be excellent (see §3) | **Blocked on one check** (§4) |
| **T2** | Ship custos-code as an **MCP server** Warp Agent can call | Medium | Weak — see §5 | **Not worth it** |

The strategy note in `DESIGN.md` §15 already says "Warp: nothing to build, demo runs inside Warp."
That remains right. This doc's contribution is the detail behind T1 and T2 so nobody re-derives it.

## 2. What Warp is, structurally

`OBSERVED` from `~/.warp/settings.toml`:

- `[general] default_session_mode = "agent"` — Warp opens in agent mode, not shell mode.
- `[terminal.input] input_box_type_setting = "universal"` — one input box routes to either the
  shell or the agent. Consequence for scripted demos: **you cannot assume typed text reaches the
  shell.** It may be interpreted as an agent prompt.
- `[agents.third_party] should_render_cli_agent_toolbar = true` — Warp detects a third-party CLI
  agent running in a pane and renders a toolbar for it. **This is the T0 mechanism**: `claude` in a
  Warp pane is a first-class, supported thing, not a hack.
- `[agents.execution_profiles.default]` denylists `bash|sh|zsh|pwsh|fish`, `curl`, `wget`, `eval`,
  `exec`, `source`, `rm`, `ssh`, `scp`, `rsync`, `dig`. `execute_commands = "agent_decides"`,
  `read_files = "always_allow"`, `apply_code_diffs = "always_ask"`.
- `[agents] cloud_conversation_storage_enabled = true` — **agent conversations are stored in
  Warp's cloud**, confirmed by `AmbientTaskUpdated message in CloudObjects::Listener` in
  `~/Library/Logs/warp.log`. This is the main risk to T1: the *report* side of the diff may not be
  on disk at all, only the *ledger* side.

## 3. Warp's local store, and why its schema is so promising

`OBSERVED`. Path (stable channel, macOS):

```
~/Library/Group Containers/2BBY89MBSN.dev.warp/Library/Application Support/dev.warp.Warp-Stable/warp.sqlite
```

WAL mode, so read it by copying `warp.sqlite` **and** `warp.sqlite-wal` before opening. The
relevant tables:

```sql
blocks(id, pane_leaf_uuid, stylized_command, stylized_output, pwd, git_branch,
       exit_code, did_execute, start_ts, completed_ts, shell, is_background,
       ai_metadata, is_local, ...)

commands(id, command, exit_code, start_ts, completed_ts, pwd, shell, username,
         hostname, session_id, git_branch, workflow_command, is_agent_executed)

agent_conversations(conversation_id, conversation_data TEXT, summary, last_modified_at)
agent_tasks(conversation_id, task_id, task BLOB, last_modified_at)
ai_queries(exchange_id, conversation_id, start_ts, input, working_directory,
           output_status, model_id, planning_model_id, coding_model_id)
```

This maps onto `LedgerEvent` almost field for field, and it is **a better fit for invariant 1 than
our Claude Code hooks are**. The hooks are written by the harness at the agent's request; Warp's
`blocks` rows are written by the *terminal*, below the agent, as a record of what the PTY actually
did. The model has no write path to it even in principle. Specifically:

| We need | Warp gives us | Notes |
|---|---|---|
| command text | `blocks.stylized_command` | Enough to detect `\| tail`, `2>/dev/null` — the `piped` flag |
| full output | `blocks.stylized_output` | The **whole** block, not what the agent chose to keep |
| exit status | `blocks.exit_code`, `did_execute` | Ground truth; `did_execute` distinguishes "typed" from "ran" |
| cwd / branch | `blocks.pwd`, `blocks.git_branch` | |
| agent vs. human | `commands.is_agent_executed`, `blocks.ai_metadata` | Lets us scope the ledger to agent actions only |
| timing | `start_ts`, `completed_ts` | |

Note also `Received CommandFinished hook` in `warp.log`: Warp installs shell-level command hooks
(preexec/precmd-style), which is how `blocks` gets its exit codes. That is the same class of
mechanism as our Claude Code hooks.

## 4. The one thing that blocks T1

`OBSERVED`: on this install, every one of those tables is **empty**, while seeded tables are not:

```
blocks=0  commands=0  agent_conversations=0  agent_tasks=0  ai_queries=0
workflows=10  notebooks=1
```

So the database is live and readable; those tables simply have no rows. Two explanations, and they
lead to opposite decisions:

1. **Fresh install.** Warp was installed the same day and essentially nothing has been run in it.
   Then T1 is fine and the adapter is a couple of hours.
2. **Warp does not persist block history locally** (or only flushes on quit, or only when cloud
   sync is off). Then T1 is dead and we should not start it.

`VERIFY` — this is a ten-second check. Run a couple of real commands **inside Warp**, then:

```bash
S="$HOME/Library/Group Containers/2BBY89MBSN.dev.warp/Library/Application Support/dev.warp.Warp-Stable"
D=$(mktemp -d); cp "$S"/warp.sqlite "$S"/warp.sqlite-wal "$D"/
sqlite3 "$D/warp.sqlite" "select count(*) from blocks; select count(*) from commands;"
```

Non-zero → explanation 1, T1 is live, and `select command, exit_code, is_agent_executed from
commands order by id desc limit 5` shows the shape. Still zero after a Warp restart → explanation 2,
close T1 and say so in `RESEARCH.md`.

**Do not start the adapter before this check comes back non-zero.** Writing a parser against a table
that never fills is exactly the failure mode `AGENTS.md` invariant 1 exists to prevent: evidence that
looks harness-written but isn't there.

## 5. Why the MCP route (T2) is weak

`OBSERVED`: Warp supports MCP — `active_mcp_servers`, `mcp_server_installations`,
`mcp_environment_variables` tables, and `mcp_allowlist` / `mcp_denylist` / `mcp_permissions` in the
default execution profile. So we *could* register custos-code as a tool Warp Agent calls.

But an MCP server sees only its own arguments. Warp does not hand MCP servers the agent's tool log,
so a `verify_my_report` tool would be verifying the agent's claims against **whatever the agent chose
to pass it** — the model writing its own evidence. That violates invariant 1 outright. The only
honest MCP tool we could ship is one that re-executes commands itself (Tier 3), which is a much
smaller product and duplicates `custos-code check --rerun`.

Revisit only if Warp exposes block context to MCP servers.

## 6. What the demo actually is

T0, and it needs no new code:

1. Open Warp. Run `claude` in a pane — Warp renders its third-party agent toolbar.
2. Give it a task where the honest answer is unpleasant (the `piped-runner` fixture: tests that
   collect zero items behind `| tail -5`).
3. The agent reports success. The `Stop` hook fires, `custos-code` builds the receipt, the gate blocks,
   and the nudge goes back — all inside a Warp block.
4. `custos-code demo --scenario honest` immediately after, to show it does not simply always accuse.

Two things to rehearse, both learned the hard way on 2026-09-19:

- **Warp's universal input box may route typed text to the agent instead of the shell.** Practise the
  exact keystrokes; do not assume a pasted command runs.
- **Do not script the demo with AppleScript/System Events keystrokes.** Focus is stolen by any app
  that raises a window, and keystrokes then land wherever focus went. If any part of the demo must be
  automated, drive it through a `--scenario` flag on our own CLI, never through simulated typing.

## 7. Open items

| ID | Question | Owner | Status |
|---|---|---|---|
| W1 | Does `blocks` populate after real use? (§4) | Oliver | open — blocks T1 |
| W2 | If W1 is yes, is `agent_conversations.conversation_data` populated locally, or cloud-only? Without it there is no report to diff against. | Ananya (adapters) | open, depends on W1 |
| W3 | `blocks.stylized_command` / `stylized_output` are `BLOB`, not `TEXT` — determine the encoding (likely a styled-span struct, not plain UTF-8) before estimating adapter cost. | Ananya | open, depends on W1 |
