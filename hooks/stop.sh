#!/usr/bin/env bash
# Claude Code Stop hook: build the receipt for this session; block only on `contradicted`.
#
# The block decision is the JSON `custos-code _hook stop` prints on stdout ({"decision": "block"}),
# which Claude Code reads directly. This wrapper's exit status is NOT the signal: a wrapper that
# exits non-zero because the checker failed to start would block a turn with no claim behind it.
# So this fails open. See hooks/_run.sh for the reasoning and the resolution order.
#
# Rules run synchronously (10 s budget); Tier 3 re-run and the judge post asynchronously.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_run.sh"
payload="$(cat)"
custos_code_hook _hook stop <<<"$payload" || true
exit 0
