#!/usr/bin/env bash
# Claude Code PostToolUse hook: append the tool call and its FULL output to the ledger.
# Captures stdout before the harness truncates it (docs/DESIGN.md §5: 42% of test output was piped away).
# Reads the hook JSON on stdin; must exit 0 quickly. NEEDS-DECISION(oliver): A5 transcript_path lag.
set -euo pipefail
payload="$(cat)"
exec uv run --quiet receipts _hook post-tool-use <<<"$payload"
