#!/usr/bin/env bash
# Claude Code PreToolUse hook: for a known-runner Bash command, rewrite it so PostToolUse can
# see the real resolved binary (E5: defeats a `./pytest` wrapper shadowing the real one).
# Reads the hook JSON on stdin; prints an updatedInput response or nothing; must exit 0 quickly.
# Fails open: if custos-code cannot run, the command goes through unrewritten rather than being
# blocked. See hooks/_run.sh.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_run.sh"
payload="$(cat)"
custos_code_hook _hook pre <<<"$payload" || true
exit 0
