import os
import subprocess
from pathlib import Path

from receipts.adapters import claude_code
from receipts.claims import extract_regex
from receipts.models import ClaimType, Verdict
from receipts.verdicts import run

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "golden", "claude_code", "session.jsonl")


def _repo(tmp_path: Path) -> Path:
    """A repo that matches the fixture's claims: middleware edited, test file created, both changed vs HEAD."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "auth").mkdir()
    (tmp_path / "auth" / "middleware.py").write_text("def middleware(request, call_next):\n    return call_next(request)\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    (tmp_path / "auth" / "middleware.py").write_text("class RateLimiter: ...\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_rate_limit.py").write_text("def test_ok():\n    assert True\n")
    return tmp_path


def test_fixture_receipt_against_matching_repo(tmp_path: Path) -> None:
    sess, ledger, report = claude_code.parse(FIXTURE)
    assert report
    claims = extract_regex(report, sess.id)
    recs = {r.claim_id: r for r in run(claims, ledger, str(_repo(tmp_path)))}
    by_type = {c.type: recs[c.id] for c in claims}
    assert by_type[ClaimType.EDIT].verdict == Verdict.CONFIRMED          # Edit event + git diff agrees
    assert by_type[ClaimType.CREATE].verdict == Verdict.CONFIRMED        # Write event + file exists
    assert by_type[ClaimType.RUN_TESTS].verdict == Verdict.UNRECORDED    # pytest | tail -5: piped
    assert by_type[ClaimType.BUILD].verdict == Verdict.UNRECORDED        # ruff ... 2>/dev/null | head: piped
    assert by_type[ClaimType.VERIFY].verdict == Verdict.UNWITNESSED      # manual curl: never contradicted
    assert by_type[ClaimType.RUN_TESTS].evidence == [10, 11]


def test_fixture_receipt_without_repo_state(tmp_path: Path) -> None:
    """Invariant 5: edit/create claims are never confirmed from the transcript alone."""
    sess, ledger, report = claude_code.parse(FIXTURE)
    claims = extract_regex(report or "", sess.id)
    recs = {r.claim_id: r for r in run(claims, ledger, str(tmp_path / "nowhere"))}
    by_type = {c.type: recs[c.id] for c in claims}
    assert by_type[ClaimType.EDIT].verdict == Verdict.UNWITNESSED
    assert by_type[ClaimType.CREATE].verdict == Verdict.UNWITNESSED
    assert by_type[ClaimType.EDIT].evidence  # the event is still cited


def test_unpiped_failing_run_is_contradicted(tmp_path: Path) -> None:
    from datetime import datetime

    from receipts.models import Claim, EventFlags, EventKind, LedgerEvent

    ts = datetime(2026, 9, 19)
    ledger = [
        LedgerEvent(seq=0, ts=ts, session_id="s", kind=EventKind.CALL, tool="Bash", input={"command": "pytest -q"}),
        LedgerEvent(seq=1, ts=ts, session_id="s", kind=EventKind.RESULT, tool="Bash",
                    output="============ test session starts ============\ncollected 12 items\n\n1 failed, 11 passed in 0.4s\n",
                    flags=EventFlags(error=True)),
    ]
    claim = Claim(id="c1", session_id="s", text="ran the suite, all 12 passing", type=ClaimType.RUN_TESTS, objects=["12"])
    (rec,) = run([claim], ledger, None)
    assert rec.verdict == Verdict.CONTRADICTED and rec.evidence == [0, 1] and "1 failed" in rec.rationale


def test_count_mismatch_is_qualified() -> None:
    from datetime import datetime

    from receipts.models import Claim, EventKind, LedgerEvent

    ts = datetime(2026, 9, 19)
    ledger = [
        LedgerEvent(seq=0, ts=ts, session_id="s", kind=EventKind.CALL, tool="Bash", input={"command": "uv run pytest"}),
        LedgerEvent(seq=1, ts=ts, session_id="s", kind=EventKind.RESULT, tool="Bash",
                    output="============ test session starts ============\ncollected 9 items\n\n9 passed in 0.2s\n"),
    ]
    claim = Claim(id="c1", session_id="s", text="all 12 tests pass", type=ClaimType.RUN_TESTS, objects=["12"])
    (rec,) = run([claim], ledger, None)
    assert rec.verdict == Verdict.QUALIFIED and rec.qualifier == "12 claimed, 9 passed"


def test_no_judge_contradiction_and_confirmed_needs_evidence() -> None:
    import pytest

    from receipts.models import VerdictRecord
    from receipts.verdicts import _enforce

    with pytest.raises(AssertionError):
        _enforce(VerdictRecord(claim_id="c", verdict=Verdict.CONTRADICTED, tier=4, method="judge", confidence=1.0))
    with pytest.raises(AssertionError):
        _enforce(VerdictRecord(claim_id="c", verdict=Verdict.CONFIRMED, tier=1, method="rule", confidence=1.0))
