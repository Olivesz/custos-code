"""Per-runner output parsers and the pipe/truncation flagger.

Each parser: (stdout, stderr, exit_code) -> RunnerResult(passed, failed, errors, collected, skipped)
or None if the output is not this runner's format. Start with pytest, jest/vitest, go test, cargo (E2).
Known traps to handle: `collected 0 items` with exit 0; `| head -80` hiding the summary;
pytest -q vs verbose; jest --silent; cargo test with multiple targets.

Property-test these with hypothesis against synthetic outputs.

Owner: Anush.
"""
from __future__ import annotations

from dataclasses import dataclass


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
