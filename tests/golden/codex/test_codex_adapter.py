import json
import os

from receipts.adapters import codex
from receipts.ledger import verify_chain
from receipts.models import EventKind

HERE = os.path.dirname(__file__)
FIXTURE = os.path.join(HERE, "session.jsonl")
EXPECTED = os.path.join(HERE, "expected_ledger.jsonl")


def _strip(e: dict) -> dict:
    # hashes are derived; compare the substantive fields
    e = dict(e)
    e.pop("hash", None)
    e.pop("prev_hash", None)
    return e


def test_parse_matches_golden() -> None:
    sess, ledger, report = codex.parse(FIXTURE)
    assert sess.source == "codex"
    assert sess.id == "0198f3c2-1f4a-7c31-9a55-6b1d2e4f7a10"
    assert sess.agent == "Codex Desktop"
    assert sess.cwd == "/home/dev/rate-limiter"
    assert verify_chain(ledger)
    assert report is not None and report.startswith("Implemented sliding-window rate limiting")
    got = [_strip(json.loads(e.model_dump_json())) for e in ledger]
    with open(EXPECTED) as fh:
        want = [_strip(json.loads(line)) for line in fh if line.strip()]
    assert got == want


def test_exec_result_carries_argv_exit_code_and_duration() -> None:
    _, ledger, _ = codex.parse(FIXTURE)
    results = [e for e in ledger if e.kind == EventKind.RESULT and e.tool == "Bash"]
    tests, deploy = results
    assert tests.exit_code == 0 and tests.duration_ms == 2250
    assert tests.flags.piped and "collected 0 items" in (tests.output or "")
    assert deploy.exit_code == 1 and not deploy.flags.piped


def test_patch_result_names_every_changed_path() -> None:
    _, ledger, _ = codex.parse(FIXTURE)
    edits = [e for e in ledger if e.tool == "Edit"]
    call, result = edits
    assert call.kind == EventKind.CALL and result.kind == EventKind.RESULT
    assert result.paths == [
        "/home/dev/rate-limiter/auth/middleware.py",
        "/home/dev/rate-limiter/tests/test_rate_limit.py",
    ]
    assert call.paths == result.paths
    assert not result.flags.error
    assert result.exit_code == 0 and result.duration_ms == 400


def test_secrets_redacted_before_storage() -> None:
    _, ledger, _ = codex.parse(FIXTURE)
    blob = json.dumps([json.loads(e.model_dump_json()) for e in ledger])
    assert "sk-abcdefghijklmnopqrstuvwx" not in blob
    assert "[REDACTED:env]" in blob


def test_turn_aborted_leaves_no_report(tmp_path) -> None:
    path = tmp_path / "rollout-aborted.jsonl"
    with open(FIXTURE) as fh:
        lines = [line for line in fh if '"task_complete"' not in line]
    lines.append(
        json.dumps(
            {
                "timestamp": "2026-09-19T14:02:21.000Z",
                "type": "event_msg",
                "payload": {"type": "turn_aborted", "reason": "user_interrupt"},
            }
        )
        + "\n"
    )
    path.write_text("".join(lines))
    _, ledger, report = codex.parse(str(path))
    assert report is None
    assert any(
        e.kind == EventKind.META and (e.input or {}).get("event") == "turn_aborted" for e in ledger
    )


def test_compaction_lowers_integrity_score(tmp_path) -> None:
    path = tmp_path / "rollout-compacted.jsonl"
    with open(FIXTURE) as fh:
        lines = fh.readlines()
    lines.insert(
        3,
        json.dumps(
            {
                "timestamp": "2026-09-19T14:02:04.000Z",
                "type": "event_msg",
                "payload": {"type": "compacted"},
            }
        )
        + "\n",
    )
    path.write_text("".join(lines))
    sess, ledger, _ = codex.parse(str(path))
    assert sess.integrity_score < 1.0
    assert any((e.input or {}).get("event") == "compacted" for e in ledger)


def test_find_last_session(tmp_path) -> None:
    day = tmp_path / "2026" / "09" / "19"
    day.mkdir(parents=True)
    a, b = day / "rollout-a.jsonl", day / "rollout-b.jsonl"
    a.write_text("{}\n")
    b.write_text("{}\n")
    os.utime(a, (1, 1))
    assert codex.find_last_session(str(tmp_path)).endswith("rollout-b.jsonl")
