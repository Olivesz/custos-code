#!/usr/bin/env bash
# Claude Code PostToolUse hook: append the tool call and its FULL output to the ledger.
# Captures stdout before the harness truncates it (docs/DESIGN.md §5: 42% of test output was piped away).
# Reads the hook JSON on stdin; must exit 0 quickly, and must never fail the turn if receipts is
# missing -- a gap in the ledger is an `unwitnessed` mark later, not an error now.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_run.sh"
payload="$(cat)"
receipts_hook _hook post-tool-use <<<"$payload" || true
exit 0
