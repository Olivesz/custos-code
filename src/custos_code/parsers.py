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
from collections.abc import Callable
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


_LABEL_RE = re.compile(r"(\d+)\s+(passed|failed|skipped|todo|total)")


def _scan_counts(text: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0, "total": 0}
    for count, label in _LABEL_RE.findall(text):
        counts[label] += int(count)
    return counts


# `pytest -q` prints neither the session banner nor "collected N items" -- only a tail like
# `86 passed in 1.2s`. Requiring the banner meant the single most common invocation parsed as
# "not a test runner at all", so `rules._outcome` fell through to its exit-code branch and
# CONFIRMED a report claiming 81 when 86 ran. Worse, `feedback` was nudging agents toward `-q`,
# so obeying the nudge flipped the verdict from contradicted to confirmed on an unchanged lie.
_PYTEST_SIGNATURE_RE = re.compile(
    r"test session starts|collected \d+ item"
    r"|^=*\s*\d+ (?:passed|failed|error|skipped|xfailed|xpassed)"
    r"|^=*\s*no tests ran",
    re.IGNORECASE | re.MULTILINE,
)
_PYTEST_COLLECTED_RE = re.compile(r"collected (\d+) item")
_PYTEST_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error(?:s)?|skipped|xfailed|xpassed)")


def parse_pytest(stdout: str, exit_code: int | None) -> RunnerResult | None:
    """Handles verbose and `-q` output, and the `collected 0 items` / exit-0 trap."""
    if not _PYTEST_SIGNATURE_RE.search(stdout):
        return None
    collected_match = _PYTEST_COLLECTED_RE.search(stdout)
    collected = int(collected_match.group(1)) if collected_match else None
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    tail = "\n".join(stdout.strip().splitlines()[-5:])
    for count, label in _PYTEST_COUNT_RE.findall(tail):
        key = "error" if label.startswith("error") else label
        if key in counts:
            counts[key] += int(count)
    return RunnerResult(
        runner="pytest",
        passed=counts["passed"],
        failed=counts["failed"],
        errors=counts["error"],
        collected=collected,
        skipped=counts["skipped"],
    )


def parse_jest(stdout: str, exit_code: int | None) -> RunnerResult | None:
    """Reads the `Tests:` summary line (`Tests:  1 failed, 4 passed, 5 total`)."""
    match = re.search(r"^Tests:\s+.*$", stdout, re.MULTILINE)
    if not match:
        return None
    counts = _scan_counts(match.group(0))
    return RunnerResult(
        runner="jest",
        passed=counts["passed"],
        failed=counts["failed"],
        errors=0,
        collected=counts["total"] or None,
        skipped=counts["skipped"],
    )


def parse_vitest(stdout: str, exit_code: int | None) -> RunnerResult | None:
    """Reads the ` Tests  1 failed | 9 passed (10)` summary line."""
    match = re.search(r"^\s*Tests\s+(.+)$", stdout, re.MULTILINE)
    if not match:
        return None
    body = match.group(1)
    counts = _scan_counts(body)
    total_match = re.search(r"\((\d+)\)", body)
    collected = int(total_match.group(1)) if total_match else (counts["total"] or None)
    return RunnerResult(
        runner="vitest",
        passed=counts["passed"],
        failed=counts["failed"],
        errors=0,
        collected=collected,
        skipped=counts["skipped"],
    )


_GO_PKG_RE = re.compile(
    r"^(?:ok\s+\S+\s+(?:\d+(?:\.\d+)?s|\(cached\))|FAIL\s+\S+\s+(?:\d+(?:\.\d+)?s|\[.+\]))",
    re.MULTILINE,
)


def parse_go_test(stdout: str, exit_code: int | None) -> RunnerResult | None:
    r"""`-v` output gives per-test `--- PASS:`/`--- FAIL:` lines; plain output only per-package ok/FAIL.

    The per-package form must carry a package *and* a timing field. `^ok\s` alone matched a bare
    `ok` line, because `\s` matches the newline -- so `echo ok` parsed as a green Go suite. That is
    not a cosmetic mislabel: `review.annotate` would print `[parsed go test: 1 passed, 0 failed]`
    next to a fabricated success, which is the `echoed_output` family the deterministic layer exists
    to catch, and `review._corroborate` accepts a parsed result as grounds to let an accusation
    stand. Measured at 40 occurrences across 15 of 96 real sessions on 2026-09-20.
    """
    if not re.search(_GO_PKG_RE.pattern + r"|^---\s+(PASS|FAIL|SKIP):", stdout, re.MULTILINE):
        return None
    passed = len(re.findall(r"^--- PASS:", stdout, re.MULTILINE))
    failed = len(re.findall(r"^--- FAIL:", stdout, re.MULTILINE))
    skipped = len(re.findall(r"^--- SKIP:", stdout, re.MULTILINE))
    if passed or failed or skipped:
        return RunnerResult(
            runner="go test",
            passed=passed,
            failed=failed,
            errors=0,
            collected=passed + failed + skipped,
            skipped=skipped,
        )
    # No -v: only per-package ok/FAIL lines, no per-test counts available.
    ok_pkgs = len(re.findall(r"^ok\s+\S+\s+(?:\d+(?:\.\d+)?s|\(cached\))", stdout, re.MULTILINE))
    fail_pkgs = len(re.findall(r"^FAIL\s+\S+\s+(?:\d+(?:\.\d+)?s|\[.+\])", stdout, re.MULTILINE))
    if ok_pkgs or fail_pkgs:
        return RunnerResult(runner="go test", passed=ok_pkgs, failed=fail_pkgs, errors=0, collected=None)
    return None


_CARGO_RESULT_RE = re.compile(
    r"test result:\s+\w+\.\s+(\d+) passed;\s+(\d+) failed;\s+(\d+) ignored;"
    r"\s+\d+ measured;\s+\d+ filtered out"
)


def parse_cargo(stdout: str, exit_code: int | None) -> RunnerResult | None:
    """Sums every `test result: ...` line, since cargo prints one per test target."""
    matches = _CARGO_RESULT_RE.findall(stdout)
    if not matches:
        return None
    passed = failed = ignored = 0
    for p, f, i in matches:
        passed += int(p)
        failed += int(f)
        ignored += int(i)
    return RunnerResult(
        runner="cargo",
        passed=passed,
        failed=failed,
        errors=0,
        collected=passed + failed + ignored,
        skipped=ignored,
    )


# E2: priority order for which runners get a parser, and which one runs first when
# more than one signature could match. Anything else falls through to `unrecorded`.
PARSERS: tuple[Callable[[str, int | None], RunnerResult | None], ...] = (
    parse_pytest,
    parse_jest,
    parse_vitest,
    parse_go_test,
    parse_cargo,
)


def parse(stdout: str, exit_code: int | None) -> RunnerResult | None:
    """Try each known runner's parser in E2 priority order. `None` means `unrecorded`."""
    for parser in PARSERS:
        result = parser(stdout, exit_code)
        if result is not None:
            return result
    return None


_PIPE_MARKERS: tuple[re.Pattern[str], ...] = (
    # wc/less/more/cut came from the Claude Code adapter's own copy of this list, which is now
    # deleted. Two detectors that disagreed meant identical evidence got opposite verdicts
    # depending on which adapter read it.
    re.compile(r"\|\s*(head|tail|grep|awk|sed|wc|less|more|cut)\b"),
    re.compile(r"2>\s*/dev/null"),
    re.compile(r"&>\s*/dev/null"),
    re.compile(r"(?:^|\s)>{1,2}\s*[\w./-]+"),  # `> file` / `>> file`, not preceded by a digit or `&`
    re.compile(r"--silent\b"),
    re.compile(r"--quiet\b"),
)


def is_piped(command: str) -> bool:
    """True if the command's output was filtered (| head, | tail, 2>/dev/null, > file, --silent...)."""
    return any(marker.search(command) for marker in _PIPE_MARKERS)


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


BIN_MARKER = "__CUSTOS_CODE_BIN="
RC_MARKER = "__CUSTOS_CODE_RC="


def wrap_command_for_resolution(command: str, rc_path: str | None = None) -> str | None:
    """PreToolUse rewrite: append a trailer resolving argv[0]'s real binary path and exit code.

    Only commands whose first token looks like a known runner are wrapped (E5's own concern is
    resolution, not general exit-code capture); heredocs, backgrounding, and trailing `&` are
    left untouched because rewriting them safely needs shell-semantics we don't have here
    (MECHANICS §2.3: "do not wrap arbitrary commands"). Returns None when nothing should change.

    `rc_path`, when given, is a random per-call path (the caller's job -- typically under
    `~/.custos-code/rc/` -- to generate and later read/unlink; see `read_rc_file`). The trailer is
    written to that file instead of stdout: the E9 experiment confirmed the stdout form is
    visible in the model's own tool output, so a model that has seen `__CUSTOS_CODE_RC=0` once can
    later `echo` the same marker and manufacture evidence for a test it never ran (issue #22). A
    file whose name the model never sees can't be forged that way. `rc_path` is embedded via a
    quoted shell assignment, not printed, so it never appears in the command's own stdout/stderr.

    `rc_path=None` keeps the legacy stdout-marker form (`BIN_MARKER`/`RC_MARKER`, stripped by
    `strip_and_parse_trailer`), for harnesses that do not preserve env across the whole compound
    command; prefer the file form whenever the caller can correlate `PreToolUse`/`PostToolUse`
    for the same call (e.g. by `tool_use_id`).
    """
    token = first_token(command)
    if token is None or not is_known_runner_token(token):
        return None
    if "<<" in command or "\n" in command or " & " in command or command.rstrip().endswith("&"):
        return None
    if rc_path is not None:
        prefix = f"CUSTOS_CODE_RC_FILE={shlex.quote(rc_path)}; export CUSTOS_CODE_RC_FILE; "
        trailer = (
            f'; __rc=$?; printf "%s\\n%s\\n" "$(command -v {shlex.quote(token)} 2>/dev/null)" "$__rc" '
            '> "$CUSTOS_CODE_RC_FILE"; exit $__rc'
        )
        return f"{prefix}({command}){trailer}"
    trailer = (
        f'; __rc=$?; printf "\\n{BIN_MARKER}%s\\n{RC_MARKER}%s\\n" '
        f'"$(command -v {shlex.quote(token)} 2>/dev/null)" "$__rc"; exit $__rc'
    )
    return f"({command}){trailer}"


def strip_and_parse_trailer(output: str) -> tuple[str, str | None, int | None]:
    """Split `wrap_command_for_resolution`'s legacy stdout trailer back out of captured output.

    Only for the `rc_path=None` (stdout) form. Returns (output with the trailer lines removed,
    resolved binary path or None, exit code or None). Used on the PostToolUse side before the
    output is stored or shown.
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


def read_rc_file(path: str) -> tuple[str | None, int | None]:
    """Parse the two-line trailer `wrap_command_for_resolution`'s file form writes.

    Format: line 1 is `command -v`'s output (resolved binary path, or empty if not found), line
    2 is the exit code. Returns (resolved binary path or None, exit code or None). A missing or
    unreadable file (the command never ran, or ran interrupted before the trailer wrote it) reads
    the same as a resolution failure: `(None, None)`. Pure read -- the caller (PostToolUse) is
    responsible for unlinking the file afterward.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None, None
    bin_path = lines[0].strip() if len(lines) > 0 else ""
    rc_text = lines[1].strip() if len(lines) > 1 else ""
    try:
        rc = int(rc_text)
    except ValueError:
        rc = None
    return (bin_path or None), rc


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
    cwd: str | None = None,
) -> bool:
    """True if a runner-shaped command's *actually resolved* binary is trustworthy evidence.

    Outside the repo tree: trusted (a system, user, or committed-elsewhere install). Inside the
    repo tree: trusted only if it lives in a dependency manager's own bin dir (`.venv`,
    `node_modules/.bin`, ...) or is named in the repo's committed test/build config
    (`documented_runners` -- the same committed-config lookup rerun.py uses to pick the Tier 3
    command, so an agent-authored `./pytest` at the repo root is never allowlisted no matter how
    it's invoked). Resolution failure (`None`, i.e. "command not found") is never trusted --
    that is positive evidence the claimed runner never ran.

    `resolved_bin` comes from `command -v` inside the wrapped command (see
    `wrap_command_for_resolution`) and is not guaranteed absolute -- observed in practice
    returning e.g. `.venv/bin/pytest` verbatim. A relative path must be resolved against the
    *ledger event's* `cwd` (the actual cwd the Bash call ran under), never the calling process's
    own ambient cwd: resolving against the wrong directory can land outside `repo_root` by
    accident and mark an in-tree shadow binary as trusted, exactly the bypass this check exists
    to prevent. If `cwd` is unknown, fail closed (untrusted) rather than guess.
    """
    if not resolved_bin:
        return False
    bin_path = Path(resolved_bin)
    if not bin_path.is_absolute():
        if not cwd:
            return False
        bin_path = Path(cwd) / bin_path
    try:
        resolved = bin_path.resolve()
        root = Path(repo_root).resolve()
    except OSError:
        return False
    if not resolved.is_relative_to(root):
        return True
    if any(p.search(str(resolved)) for p in _TRUSTED_IN_TREE_PATTERNS):
        return True
    return resolved.name in documented_runners
