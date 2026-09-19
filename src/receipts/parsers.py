"""Per-runner output parsers and the pipe/truncation flagger.

Each parser: (stdout, stderr, exit_code) -> RunnerResult(passed, failed, errors, collected, skipped)
or None if the output is not this runner's format. Start with pytest, jest/vitest, go test, cargo (E2).
Known traps to handle: `collected 0 items` with exit 0; `| head -80` hiding the summary;
pytest -q vs verbose; jest --silent; cargo test with multiple targets.

Property-test these with hypothesis against synthetic outputs.

Owner: Anush.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


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


_PYTEST_SIGNATURE_RE = re.compile(r"test session starts|collected \d+ item", re.IGNORECASE)
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


def parse_go_test(stdout: str, exit_code: int | None) -> RunnerResult | None:
    """`-v` output gives per-test `--- PASS:`/`--- FAIL:` lines; plain output only per-package ok/FAIL."""
    if not re.search(r"^(ok|FAIL)\s|^---\s+(PASS|FAIL|SKIP):", stdout, re.MULTILINE):
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
    ok_pkgs = len(re.findall(r"^ok\s", stdout, re.MULTILINE))
    fail_pkgs = len(re.findall(r"^FAIL\s", stdout, re.MULTILINE))
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
    re.compile(r"\|\s*(head|tail|grep|awk|sed)\b"),
    re.compile(r"2>\s*/dev/null"),
    re.compile(r"&>\s*/dev/null"),
    re.compile(r"(?:^|\s)>{1,2}\s*[\w./-]+"),  # `> file` / `>> file`, not preceded by a digit or `&`
    re.compile(r"--silent\b"),
    re.compile(r"--quiet\b"),
)


def is_piped(command: str) -> bool:
    """True if the command's output was filtered (| head, | tail, 2>/dev/null, > file, --silent...)."""
    return any(marker.search(command) for marker in _PIPE_MARKERS)
