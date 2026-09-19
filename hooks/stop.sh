#!/usr/bin/env bash
# Claude Code Stop hook: build the receipt for this session; block only on `contradicted`.
# Exit 2 with the evidence on stderr makes Claude Code continue with that text as feedback.
# Rules run synchronously (10 s budget); Tier 3 re-run and the judge post asynchronously.
set -euo pipefail
payload="$(cat)"
if ! uv run --quiet receipts _hook stop <<<"$payload"; then
  exit 2
fi
