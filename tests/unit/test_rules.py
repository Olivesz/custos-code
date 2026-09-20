import os
import subprocess
from pathlib import Path

from custos_code.adapters import claude_code
from custos_code.claims import extract_regex
from custos_code.models import ClaimType, Verdict
from custos_code.verdicts import run

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

    from custos_code.models import Claim, EventFlags, EventKind, LedgerEvent

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

    from custos_code.models import Claim, EventKind, LedgerEvent

    ts = datetime(2026, 9, 19)
    ledger = [
        LedgerEvent(seq=0, ts=ts, session_id="s", kind=EventKind.CALL, tool="Bash", input={"command": "uv run pytest"}),
        LedgerEvent(seq=1, ts=ts, session_id="s", kind=EventKind.RESULT, tool="Bash",
                    output="============ test session starts ============\ncollected 9 items\n\n9 passed in 0.2s\n"),
    ]
    claim = Claim(id="c1", session_id="s", text="all 12 tests pass", type=ClaimType.RUN_TESTS, objects=["12"])
    (rec,) = run([claim], ledger, None)
    assert rec.verdict == Verdict.QUALIFIED and rec.qualifier == "12 claimed, 9 passed"


def test_piped_test_claim_starts_tier3_rerun(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from datetime import datetime

    import custos_code.verdicts as verdicts
    from custos_code import rerun
    from custos_code.models import Claim, EventFlags, EventKind, LedgerEvent

    ts = datetime(2026, 9, 19)
    ledger = [
        LedgerEvent(seq=0, ts=ts, session_id="s", kind=EventKind.CALL, tool="Bash",
                    input={"command": "pytest -q | tail -5"}),
        LedgerEvent(seq=1, ts=ts, session_id="s", kind=EventKind.RESULT, tool="Bash",
                    output="collected 12 items\n", flags=EventFlags(piped=True)),
    ]
    claim = Claim(id="c1", session_id="s", text="all 12 tests pass", type=ClaimType.RUN_TESTS, objects=["12"])
    spawned: dict[str, object] = {}

    monkeypatch.setattr(verdicts.rerun, "poll", lambda session_id, claim_id: rerun.RerunStatus.NONE)
    monkeypatch.setattr(verdicts.rerun, "can_rerun_tests", lambda repo_root: True)

    def fake_spawn(session_id: str, claim_id: str, repo_root: str, report_seq: int) -> None:
        spawned.update(session_id=session_id, claim_id=claim_id, repo_root=repo_root, report_seq=report_seq)

    monkeypatch.setattr(verdicts.rerun, "spawn_async", fake_spawn)

    (rec,) = run([claim], ledger, str(tmp_path))

    assert rec.verdict == Verdict.UNRECORDED
    assert rec.tier == 3 and rec.method == "rerun"
    assert "started" in rec.rationale
    assert spawned == {"session_id": "s", "claim_id": "c1", "repo_root": str(tmp_path), "report_seq": 1}


def test_completed_tier3_rerun_settles_test_claim(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from datetime import datetime

    import custos_code.verdicts as verdicts
    from custos_code import rerun
    from custos_code.models import Claim, EventFlags, EventKind, LedgerEvent

    ts = datetime(2026, 9, 19)
    ledger = [
        LedgerEvent(seq=0, ts=ts, session_id="s", kind=EventKind.CALL, tool="Bash",
                    input={"command": "pytest -q | tail -5"}),
        LedgerEvent(seq=1, ts=ts, session_id="s", kind=EventKind.RESULT, tool="Bash",
                    output="collected 12 items\n", flags=EventFlags(piped=True)),
    ]
    result = LedgerEvent(
        seq=7, ts=ts, session_id="s", kind=EventKind.RERUN, tool="rerun_tests",
        output="============ test session starts ============\ncollected 12 items\n\n12 passed in 0.4s\n",
        exit_code=0,
    )
    claim = Claim(id="c1", session_id="s", text="all 12 tests pass", type=ClaimType.RUN_TESTS, objects=["12"])

    monkeypatch.setattr(verdicts.rerun, "poll", lambda session_id, claim_id: rerun.RerunStatus.DONE)
    monkeypatch.setattr(verdicts.rerun, "load_result", lambda session_id, claim_id: result)
    monkeypatch.setattr(verdicts.rerun, "can_rerun_tests", lambda repo_root: True)

    (rec,) = run([claim], ledger, str(tmp_path))

    assert rec.verdict == Verdict.CONFIRMED
    assert rec.tier == 3 and rec.method == "rerun"
    assert rec.evidence == [7]
    assert "12 passed" in rec.rationale


def test_tier3_does_not_spawn_without_repo_state(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from datetime import datetime

    import custos_code.verdicts as verdicts
    from custos_code.models import Claim, EventFlags, EventKind, LedgerEvent

    ts = datetime(2026, 9, 19)
    ledger = [
        LedgerEvent(seq=0, ts=ts, session_id="s", kind=EventKind.CALL, tool="Bash",
                    input={"command": "pytest -q | tail -5"}),
        LedgerEvent(seq=1, ts=ts, session_id="s", kind=EventKind.RESULT, tool="Bash",
                    output="collected 12 items\n", flags=EventFlags(piped=True)),
    ]
    claim = Claim(id="c1", session_id="s", text="all 12 tests pass", type=ClaimType.RUN_TESTS, objects=["12"])

    def fail_spawn(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tier 3 should not spawn without a repo root")

    monkeypatch.setattr(verdicts.rerun, "spawn_async", fail_spawn)

    (rec,) = run([claim], ledger, None)

    assert rec.verdict == Verdict.UNRECORDED
    assert rec.tier == 2 and rec.method == "rule"


def test_no_judge_contradiction_and_confirmed_needs_evidence() -> None:
    import pytest

    from custos_code.models import VerdictRecord
    from custos_code.verdicts import _enforce

    with pytest.raises(AssertionError):
        _enforce(VerdictRecord(claim_id="c", verdict=Verdict.CONTRADICTED, tier=4, method="judge", confidence=1.0))
    with pytest.raises(AssertionError):
        _enforce(VerdictRecord(claim_id="c", verdict=Verdict.CONFIRMED, tier=1, method="rule", confidence=1.0))
