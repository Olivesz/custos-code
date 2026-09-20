#!/usr/bin/env bash
# Shared runner resolution for the Claude Code hooks. Sourced, not executed.
#
# Why this exists: the hooks used to hardcode `uv run receipts ...`. On a machine without `uv` on
# PATH -- which includes a plain `git clone` of this repo, and included Oliver's own laptop on
# 2026-09-19 -- that fails, and because stop.sh ran under `set -e` and converted any non-zero exit
# into `exit 2`, Claude Code read the missing binary as "the gate says block". A machine with a
# broken install would therefore block every single turn with no claim behind it.
#
# That is the worst failure this project can have. AGENTS.md invariant: `contradicted` requires
# positive evidence. "The checker did not start" is not evidence of anything, so it must never
# block. Hooks here FAIL OPEN: if receipts cannot run, warn on stderr and exit 0.
#
# Blocking is signalled by the JSON the command prints on stdout (`{"decision": "block"}`), which
# `receipts _hook stop` emits on its own -- never by the wrapper's exit status.
#
# Owner: Oliver.

_receipts_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

# Echoes a runnable command prefix, or nothing if receipts cannot be run at all.
receipts_cmd() {
  local root; root="$(_receipts_root)"

  # 1. Explicit override wins, for CI and for unusual installs.
  if [[ -n "${RECEIPTS_BIN:-}" && -x "${RECEIPTS_BIN}" ]]; then
    echo "${RECEIPTS_BIN}"; return 0
  fi
  # 2. The repo's own virtualenv: the common case for a checkout, and needs nothing on PATH.
  if [[ -x "${root}/.venv/bin/receipts" ]]; then
    echo "${root}/.venv/bin/receipts"; return 0
  fi
  if [[ -x "${root}/.venv/bin/python" ]]; then
    echo "${root}/.venv/bin/python -m receipts"; return 0
  fi
  # 3. An installed console script.
  if command -v receipts >/dev/null 2>&1; then
    echo "receipts"; return 0
  fi
  # 4. uv, if the user happens to have it.
  if command -v uv >/dev/null 2>&1; then
    echo "uv run --quiet receipts"; return 0
  fi
  return 1
}

# Run a receipts hook subcommand with the payload on stdin. Always returns 0 unless the command
# itself ran and chose to fail; a resolution failure is reported and swallowed.
receipts_hook() {
  local cmd
  if ! cmd="$(receipts_cmd)"; then
    echo "receipts: no runnable install found (tried \$RECEIPTS_BIN, .venv, PATH, uv);" \
         "not blocking. See hooks/_run.sh." >&2
    return 1
  fi
  # shellcheck disable=SC2086
  ${cmd} "$@"
}
