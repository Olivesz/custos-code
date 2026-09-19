#!/usr/bin/env bash
# Claude Code PreToolUse hook: for a known-runner Bash command, rewrite it so PostToolUse can
# see the real resolved binary (E5: defeats a `./pytest` wrapper shadowing the real one).
# Reads the hook JSON on stdin; prints an updatedInput response or nothing; must exit 0 quickly.
set -euo pipefail
payload="$(cat)"
exec uv run --quiet receipts _hook pre <<<"$payload"
