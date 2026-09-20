from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from custos_code.models import Claim, ClaimType, EventFlags, EventKind, LedgerEvent, Verdict
from custos_code.verdicts import run

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "bench" / "fixtures"


def _init_fixture_repo(src: Path, tmp_path: Path) -> Path:
    repo = tmp_path / src.parent.name
    shutil.copytree(src, repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "bench@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Bench"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repo, check=True)
    return repo


def _oracle(name: str) -> dict:
    with open(FIXTURES / name / "oracle.json", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.mark.parametrize("name", ["piped-runner", "broken-runner"])
def test_bench_fixture_oracle_command_describes_real_repo(name: str, tmp_path: Path) -> None:
    oracle = _oracle(name)
    repo = _init_fixture_repo(FIXTURES / name / "repo", tmp_path)

    # The fixture says `python`, which is the right thing to record in an oracle: it describes
    # what a developer would type. Resolving it to the interpreter running the tests is the
    # test's job -- a bare `python` on this machine is a system build with no pytest, so the
    # oracle "failed" for a reason that has nothing to do with the fixture it is checking.
    command = list(oracle["oracle_command"])
    if command[:1] == ["python"]:
        command[0] = sys.executable

    proc = subprocess.run(
        command,
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    expect = oracle["oracle_expect"]
    assert proc.returncode == expect["exit_code"]
    assert expect["contains"] in proc.stdout + proc.stderr


@pytest.mark.parametrize("name", ["piped-runner", "broken-runner"])
def test_bench_fixture_false_report_has_expected_verdict(name: str, tmp_path: Path) -> None:
    oracle = _oracle(name)
    repo = _init_fixture_repo(FIXTURES / name / "repo", tmp_path)
    row = oracle["false_report_ledger"][0]
    ts = datetime(2026, 9, 20, tzinfo=UTC)
    ledger = [
        LedgerEvent(
            seq=0,
            ts=ts,
            session_id=name,
            kind=EventKind.CALL,
            tool="Bash",
            input={"command": row["command"]},
            cwd=str(repo),
        ),
        LedgerEvent(
            seq=1,
            ts=ts,
            session_id=name,
            kind=EventKind.RESULT,
            tool="Bash",
            output=row["output"],
            exit_code=row["exit_code"],
            cwd=str(repo),
            flags=EventFlags.model_validate(row.get("flags", {})),
        ),
    ]
    expected = oracle["expected_receipt"]
    claim = Claim(
        id="c1",
        session_id=name,
        text=oracle["claim"],
        type=ClaimType(expected["claim_type"]),
    )

    (record,) = run([claim], ledger, str(repo))

    assert record.verdict == Verdict(expected["verdict"])
    assert expected["reason_contains"] in record.rationale
