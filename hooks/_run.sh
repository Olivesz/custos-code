#!/usr/bin/env bash
# Shared runner resolution for the Claude Code hooks. Sourced, not executed.
#
# Why this exists: the hooks used to hardcode `uv run custos-code ...`. On a machine without `uv` on
# PATH -- which includes a plain `git clone` of this repo, and included Oliver's own laptop on
# 2026-09-19 -- that fails, and because stop.sh ran under `set -e` and converted any non-zero exit
# into `exit 2`, Claude Code read the missing binary as "the gate says block". A machine with a
# broken install would therefore block every single turn with no claim behind it.
#
# That is the worst failure this project can have. AGENTS.md invariant: `contradicted` requires
# positive evidence. "The checker did not start" is not evidence of anything, so it must never
# block. Hooks here FAIL OPEN: if custos-code cannot run, warn on stderr and exit 0.
#
# Blocking is signalled by the JSON the command prints on stdout (`{"decision": "block"}`), which
# `custos-code _hook stop` emits on its own -- never by the wrapper's exit status.
#
# Owner: Oliver.

_custos_code_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

# Echoes a runnable command prefix, or nothing if custos-code cannot be run at all.
custos_code_cmd() {
  local root; root="$(_custos_code_root)"

  # 1. Explicit override wins, for CI and for unusual installs.
  if [[ -n "${CUSTOS_CODE_BIN:-}" && -x "${CUSTOS_CODE_BIN}" ]]; then
    echo "${CUSTOS_CODE_BIN}"; return 0
  fi
  # 2. The repo's own virtualenv: the common case for a checkout, and needs nothing on PATH.
  if [[ -x "${root}/.venv/bin/custos-code" ]]; then
    echo "${root}/.venv/bin/custos-code"; return 0
  fi
  if [[ -x "${root}/.venv/bin/python" ]]; then
    echo "${root}/.venv/bin/python -m custos_code"; return 0
  fi
  # 3. An installed console script.
  if command -v custos-code >/dev/null 2>&1; then
    echo "custos-code"; return 0
  fi
  # 4. uv, if the user happens to have it.
  if command -v uv >/dev/null 2>&1; then
    echo "uv run --quiet custos-code"; return 0
  fi
  return 1
}

# Run a custos-code hook subcommand with the payload on stdin. Always returns 0 unless the command
# itself ran and chose to fail; a resolution failure is reported and swallowed.
custos_code_hook() {
  local cmd
  if ! cmd="$(custos_code_cmd)"; then
    echo "custos-code: no runnable install found (tried \$CUSTOS_CODE_BIN, .venv, PATH, uv);" \
         "not blocking. See hooks/_run.sh." >&2
    return 1
  fi
  # shellcheck disable=SC2086
  ${cmd} "$@"
}
