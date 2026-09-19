"""Per-runner output parsers and the pipe/truncation flagger.

Each parser: (stdout, stderr, exit_code) -> RunnerResult(passed, failed, errors, collected, skipped)
or None if the output is not this runner's format. Start with pytest, jest/vitest, go test, cargo (E2).
Known traps to handle: `collected 0 items` with exit 0; `| head -80` hiding the summary;
pytest -q vs verbose; jest --silent; cargo test with multiple targets.

Property-test these with hypothesis against synthetic outputs.

Owner: Anush.

Also owns E5 (wrapper-shadowing detection): normalizing a Bash command down to the binary
that would actually run, wrapping known-runner commands in PreToolUse so PostToolUse can see
the real resolved path (piggybacks on the same trailer trick used for exit codes, E9), and the
trust rule that rejects an in-tree `./pytest` unless it lives in a dependency manager's own
bin dir or is named by the repo's committed test/build config. See docs/MECHANICS.md §2, §4.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunnerResult:
    runner: str
    passed: int
    failed: int
    errors: int
    collected: int | None
    skipped: int = 0


def parse_pytest(stdout: str, exit_code: int | None) -> RunnerResult | None:
    raise NotImplementedError


def is_piped(command: str) -> bool:
    """True if the command's output was filtered (| head, | tail, 2>/dev/null, > file, --silent...)."""
    raise NotImplementedError


# --- E5: runner-binary resolution and wrapper-shadowing detection ---

KNOWN_RUNNERS = frozenset({
    "pytest", "jest", "vitest", "go", "cargo", "gradle", "gradlew", "xcodebuild",
    "mvn", "bun", "npm", "yarn", "pnpm", "tox", "nox", "rspec", "phpunit",
})

_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")
_CD_PREFIX = re.compile(r"^cd\s+\S+\s*&&\s*(.*)$")
# Prefixes MECHANICS.md §4 says to strip before the first remaining token is the runner.
_STRIP_PREFIXES = ("uv run", "npx", "poetry run", "bunx", "pipenv run")


def _tokenize_normalized(command: str) -> list[str]:
    """Strip a leading `cd x &&`, env assignments, and known invoker prefixes. MECHANICS §4."""
    cmd = command.strip()
    m = _CD_PREFIX.match(cmd)
    if m:
        cmd = m.group(1).strip()
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    while tokens and _ENV_ASSIGNMENT.match(tokens[0]):
        tokens.pop(0)
    for prefix in _STRIP_PREFIXES:
        prefix_tokens = prefix.split()
        if tokens[: len(prefix_tokens)] == prefix_tokens:
            tokens = tokens[len(prefix_tokens):]
            break
    return tokens


def first_token(command: str) -> str | None:
    """The binary the normalized command would actually invoke, e.g. `./pytest` or `pytest`."""
    tokens = _tokenize_normalized(command)
    return tokens[0] if tokens else None


def is_known_runner_token(token: str) -> bool:
    """True if a command's first token is shaped like a known test/build runner's name.

    Matches on basename so `./pytest`, `/usr/bin/pytest`, and `pytest` all match -- this is a
    cheap pre-filter for which commands are worth wrapping, not a trust decision.
    """
    return Path(token).name in KNOWN_RUNNERS


BIN_MARKER = "__RECEIPTS_BIN="
RC_MARKER = "__RECEIPTS_RC="


def wrap_command_for_resolution(command: str) -> str | None:
    """PreToolUse rewrite: append a trailer resolving argv[0]'s real binary path and exit code.

    Only commands whose first token looks like a known runner are wrapped (E5's own concern is
    resolution, not general exit-code capture); heredocs, backgrounding, and trailing `&` are
    left untouched because rewriting them safely needs shell-semantics we don't have here
    (MECHANICS §2.3: "do not wrap arbitrary commands"). Returns None when nothing should change.

    VERIFY(E9/E5): whether the rewritten command is what the model sees in its own context: if
    so, the trailer should be built so it never appears in what we show back to the model.
    """
    token = first_token(command)
    if token is None or not is_known_runner_token(token):
        return None
    if "<<" in command or "\n" in command or " & " in command or command.rstrip().endswith("&"):
        return None
    trailer = (
        f'; __rc=$?; printf "\\n{BIN_MARKER}%s\\n{RC_MARKER}%s\\n" '
        f'"$(command -v {shlex.quote(token)} 2>/dev/null)" "$__rc"; exit $__rc'
    )
    return f"({command}){trailer}"


def strip_and_parse_trailer(output: str) -> tuple[str, str | None, int | None]:
    """Split `wrap_command_for_resolution`'s trailer back out of captured output.

    Returns (output with the trailer lines removed, resolved binary path or None, exit code or
    None). Used on the PostToolUse side before the output is stored or shown.
    """
    bin_path: str | None = None
    rc: int | None = None
    kept: list[str] = []
    for line in output.splitlines():
        if line.startswith(BIN_MARKER):
            bin_path = line[len(BIN_MARKER):].strip() or None
        elif line.startswith(RC_MARKER):
            try:
                rc = int(line[len(RC_MARKER):].strip())
            except ValueError:
                rc = None
        else:
            kept.append(line)
    return "\n".join(kept), bin_path, rc


_TRUSTED_IN_TREE_PATTERNS = (
    re.compile(r"(^|/)\.venv/.*bin/"),
    re.compile(r"(^|/)venv/.*bin/"),
    re.compile(r"(^|/)node_modules/\.bin/"),
    re.compile(r"(^|/)\.tox/.*bin/"),
    re.compile(r"(^|/)vendor/bundle/.*bin/"),
)


def is_trusted_runner_path(
    resolved_bin: str | None,
    repo_root: str,
    documented_runners: frozenset[str] = frozenset(),
) -> bool:
    """True if a runner-shaped command's *actually resolved* binary is trustworthy evidence.

    Outside the repo tree: trusted (a system, user, or committed-elsewhere install). Inside the
    repo tree: trusted only if it lives in a dependency manager's own bin dir (`.venv`,
    `node_modules/.bin`, ...) or is named in the repo's committed test/build config
    (`documented_runners` -- the same committed-config lookup rerun.py uses to pick the Tier 3
    command, so an agent-authored `./pytest` at the repo root is never allowlisted no matter how
    it's invoked). Resolution failure (`None`, i.e. "command not found") is never trusted --
    that is positive evidence the claimed runner never ran.
    """
    if not resolved_bin:
        return False
    try:
        resolved = Path(resolved_bin).resolve()
        root = Path(repo_root).resolve()
    except OSError:
        return False
    if not resolved.is_relative_to(root):
        return True
    if any(p.search(str(resolved)) for p in _TRUSTED_IN_TREE_PATTERNS):
        return True
    return resolved.name in documented_runners
