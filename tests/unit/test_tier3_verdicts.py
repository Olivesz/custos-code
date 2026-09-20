"""Tier 3 must reach a cited verdict, without executing jobs during audits."""
import json
from pathlib import Path

import pytest

from custos_code import hooks, rerun, verdicts
from custos_code.models import Claim, ClaimType, EventKind, LedgerEvent, Verdict


def claim() -> Claim:
    return Claim(id="c1", session_id="s", text="All 3 tests pass", type=ClaimType.RUN_TESTS,
                 objects=["3"])


def event(**updates: object) -> LedgerEvent:
    data = dict(seq=2, ts="2026-09-20T00:00:00Z", session_id="s", kind="rerun",
                tool="rerun_tests", input={"claim_id": "c1", "report_seq": 1, "claim_text": "All 3 tests pass"},
                output="collected 3 items\n3 passed in 0.1s", exit_code=0)
    data.update(updates)
    return LedgerEvent.model_validate(data)


@pytest.mark.parametrize("output,code,flags,expected", [
    ("collected 3 items\n3 passed in 0.1s", 0, {}, Verdict.CONFIRMED),
    ("collected 3 items\n1 failed, 2 passed in 0.1s", 1, {}, Verdict.CONTRADICTED),
    ("collected 0 items\nno tests ran", 5, {}, Verdict.CONTRADICTED),
    ("collected 2 items\n2 passed in 0.1s", 0, {}, Verdict.QUALIFIED),
    ("collected 3 items\n3 passed in 0.1s", None, {}, Verdict.UNRECORDED),
    ("collected 3 items\n3 passed in 0.1s", 0, {"truncated": True}, Verdict.UNRECORDED),
    ("collected 3 items\n3 passed in 0.1s", 0, {"timed_out": True}, Verdict.UNRECORDED),
    ("collected 3 items\n3 passed in 0.1s", 0, {"interrupted": True}, Verdict.UNRECORDED),
    ("collected 3 items\n3 passed in 0.1s", 1, {}, Verdict.UNRECORDED),
    ("success", 0, {}, Verdict.UNRECORDED),
    ("python: No module named pytest", 1, {}, Verdict.UNRECORDED),
])
def test_outcome(output: str, code: int | None, flags: dict, expected: Verdict) -> None:
    rec, = verdicts.run([claim()], [event(output=output, exit_code=code, flags=flags)], None)
    assert rec.verdict == expected
    assert (rec.tier, rec.method, rec.evidence) == (3, "rerun", [2])


@pytest.mark.parametrize("updates", [
    {"session_id": "other"}, {"flags": {"sidechain": True}}, {"tool": "Bash"},
    {"input": {"claim_id": "other", "report_seq": 1}}, {"input": {}},
    {"input": {"claim_id": "c1", "report_seq": 1, "claim_text": "All 30 tests pass"}},
    {"seq": 0}, {"input": {"claim_id": "c1", "report_seq": "1"}},
])
def test_unrelated_or_unnumbered_results_are_not_used(updates: dict) -> None:
    rec, = verdicts.run([claim()], [event(**updates)], None)
    assert rec.verdict == Verdict.UNWITNESSED


def test_later_activity_invalidates_a_result() -> None:
    later = event(seq=3, kind=EventKind.CALL, tool="Edit")
    rec, = verdicts.run([claim()], [event(), later], None)
    assert rec.verdict == Verdict.UNWITNESSED


def test_builds_and_negative_claims_cannot_use_test_results() -> None:
    for c in (claim().model_copy(update={"type": ClaimType.BUILD}),
              claim().model_copy(update={"polarity": "did_not"})):
        rec, = verdicts.run([c], [event()], None)
        assert rec.verdict == Verdict.UNWITNESSED


def test_audit_never_launches_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("audit launched a job")
    monkeypatch.setattr(rerun, "spawn_async", fail)
    verdicts.run([claim()], [], ".")


def test_worker_result_survives_collection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rerun, "_sessions_root", lambda: tmp_path)
    monkeypatch.setattr(rerun, "rerun_tests", lambda *a, **k: event(seq=0, input={}))
    d = rerun._rerun_dir("s")
    (d / "c1.pending.json").write_text(json.dumps({
        "repo_root": str(tmp_path), "report_seq": 1, "timeout_s": 60,
        "claim_text": "All 3 tests pass",
    }))
    rerun.run_worker("s", "c1")
    base = [event(seq=1, kind=EventKind.RESULT, flags={"piped": True})]
    ledger = hooks._collect_reruns("s", base, str(tmp_path / "absent.jsonl"))
    rec, = verdicts.run([claim()], ledger, None)
    assert (rec.verdict, rec.tier, rec.evidence) == (Verdict.CONFIRMED, 3, [2])


def test_gold_tier3_cases() -> None:
    cases = json.loads((Path(__file__).parents[1] / "golden/tier3/cases.json").read_text())
    for case in cases:
        rec, = verdicts.run([claim()], [event(**case["event"])], None)
        assert rec.verdict.value == case["verdict"], case["name"]


@pytest.mark.parametrize("with_backend", [False, True])
def test_stop_emits_tier3_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                with_backend: bool) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(hooks, "HOME", str(tmp_path / "hooks"))
    monkeypatch.setattr(rerun, "_sessions_root", lambda: tmp_path / "sessions")
    monkeypatch.delenv("CUSTOS_CODE_ONLY_IN", raising=False)
    monkeypatch.setattr(hooks.judge_mod, "make_backend", lambda: object() if with_backend else None)
    c = claim()
    monkeypatch.setattr(hooks.claims_mod, "extract", lambda *a: [c])
    open_rec, = verdicts.run([c], [], None)
    monkeypatch.setattr(hooks.review_mod, "review", lambda *a, **k: SimpleNamespace(
        claims=[c], verdicts=[open_rec]))
    live, _, receipt = hooks._paths("s")
    rows = [event(seq=0, kind=EventKind.CALL, tool="Bash", input={"command": "pytest"}),
            event(seq=1, kind=EventKind.RESULT, flags={"piped": True})]
    Path(live).write_text("".join(e.model_dump_json() + "\n" for e in rows))
    (rerun._rerun_dir("s") / "c1.result.json").write_text(event(seq=0).model_dump_json())
    hooks.on_stop({"session_id": "s", "cwd": str(tmp_path), "last_assistant_message": c.text})
    text = Path(receipt).read_text()
    assert "confirmed" in text and "Tier 3" in text
