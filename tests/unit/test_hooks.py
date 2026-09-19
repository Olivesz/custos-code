import json
import os
from pathlib import Path

from receipts import hooks
from receipts.claims import extract_regex
from receipts.feedback import build_block_reason, nudge
from receipts.models import Verdict
from receipts.verdicts import run

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "golden", "claude_code", "session.jsonl")


def _use_home(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(hooks, "HOME", str(tmp_path / ".receipts"))


def test_post_tool_use_appends_call_and_result(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _use_home(tmp_path, monkeypatch)
    hooks.on_post_tool_use({"session_id": "s1", "cwd": "/w", "tool_name": "Bash", "tool_use_id": "t1",
                            "tool_input": {"command": "pytest | tail -5"}, "tool_response": "collected 0 items"})
    live = tmp_path / ".receipts" / "live" / "s1.jsonl"
    lines = [json.loads(line) for line in live.read_text().splitlines()]
    assert [x["kind"] for x in lines] == ["call", "result"]
    assert lines[1]["flags"]["piped"] is True and lines[1]["output"] == "collected 0 items"


def test_stop_manual_mode_writes_receipt_and_does_not_block(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _use_home(tmp_path, monkeypatch)
    out = hooks.on_stop({"session_id": "11111111-2222-3333-4444-555555555555", "transcript_path": FIXTURE, "cwd": str(tmp_path),
                         "last_assistant_message": "I ran the full suite, all 12 passing. Ready to merge.", "stop_hook_active": False})
    assert out is None
    receipt = (tmp_path / ".receipts" / "receipts" / "11111111-2222-3333-4444-555555555555.txt").read_text()
    assert "unrecorded" in receipt and "piped" in receipt


def test_stop_auto_mode_blocks_with_deterministic_nudges(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _use_home(tmp_path, monkeypatch)
    (tmp_path / ".receipts").mkdir()
    (tmp_path / ".receipts" / "config.toml").write_text('[tiers]\nauto = true\nauto_max_passes = 2\n')
    payload = {"session_id": "11111111-2222-3333-4444-555555555555", "transcript_path": FIXTURE, "cwd": str(tmp_path),
               "last_assistant_message": "I ran the full suite, all 12 passing, lint is clean, and verified the endpoint manually with curl.",
               "stop_hook_active": False}
    out = hooks.on_stop(payload)
    assert out is not None and out["decision"] == "block"
    reason = out["reason"]
    assert "pass 1 of 2" in reason and "`pytest -q`" in reason and "ruff check ." in reason and "withdraw" in reason
    # second pass with the same report: still blocked (nothing new in the ledger); third: cap hit, hand back
    payload["stop_hook_active"] = True
    assert hooks.on_stop(payload) is not None
    assert hooks.on_stop(payload) is None


def test_nudge_templates_are_deterministic() -> None:
    from receipts.adapters import claude_code

    sess, ledger, report = claude_code.parse(FIXTURE)
    claims = extract_regex(report or "", sess.id)
    recs = run(claims, ledger, "/nonexistent")
    pairs = [(c, r) for c, r in zip(claims, recs, strict=True) if r.verdict != Verdict.CONFIRMED]
    a = build_block_reason(pairs, ledger, 1, 3)
    b = build_block_reason(pairs, ledger, 1, 3)
    assert a == b
    for c, r in pairs:
        n = nudge(c, r, ledger)
        assert n is not None and c.text in n


def test_rewording_without_new_evidence_does_not_clear(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A claim that was open, then 'confirms' on evidence older than the nudge, stays open."""
    _use_home(tmp_path, monkeypatch)
    (tmp_path / ".receipts").mkdir()
    (tmp_path / ".receipts" / "config.toml").write_text("[tiers]\nauto = true\nauto_max_passes = 3\n")
    sid = "11111111-2222-3333-4444-555555555555"
    # pass 1: the create claim is open because repo state is unavailable (unwitnessed)
    p1 = {"session_id": sid, "transcript_path": FIXTURE, "cwd": "/nonexistent",
          "last_assistant_message": "added 12 tests in tests/test_rate_limit.py", "stop_hook_active": False}
    assert hooks.on_stop(p1) is not None
    # pass 2: same claim text, repo now 'exists' so state would confirm it, but no new ledger events since the nudge
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_rate_limit.py").write_text("def test_ok(): pass\n")
    p2 = dict(p1, cwd=str(repo), stop_hook_active=True)
    out = hooks.on_stop(p2)
    assert out is not None and "not cleared" in out["reason"]
