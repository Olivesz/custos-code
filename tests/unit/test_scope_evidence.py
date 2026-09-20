"""The evaluation denominator must not treat missing coverage as a successful scope check."""
from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

from custos_code.models import EventKind, LedgerEvent, Session

spec = importlib.util.spec_from_file_location(
    "scope_evidence", Path(__file__).resolve().parents[2] / "eval/scope_evidence.py",
)
assert spec and spec.loader
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def call(seq=0, tool="Bash", inp=None, cwd="/synthetic", second=0):
    return LedgerEvent(seq=seq, session_id="synthetic", kind=EventKind.CALL, tool=tool,
                       input=inp or {"command": "git push"}, cwd=cwd,
                       ts=datetime(2026, 1, 1, 0, 0, second, tzinfo=UTC))


def run_corpus(tmp_path, monkeypatch, records):
    for name in records:
        (tmp_path / name).touch()
    session = Session(id="synthetic", source="claude_code", agent="claude-code",
                      started=datetime(2026, 1, 1, tzinfo=UTC), cwd="/synthetic")
    monkeypatch.setattr(study.claude_code, "parse",
                        lambda p: (session, records[Path(p).name], None))
    return study.evaluate({"claude_code": tmp_path})


def test_deduplicate_copied_history_but_keep_later_repeated_action(tmp_path, monkeypatch):
    report, review = run_corpus(tmp_path, monkeypatch, {
        "a.jsonl": [call()], "b.jsonl": [call(), call(seq=1, second=1)],
    })
    data = report["sources"]["claude_code"]
    assert data["counts"]["duplicate_calls"] == 1
    assert data["counts"]["evaluated_calls"] == 2
    assert data["bands"] == {"yellow": 2}
    assert len(review) == 2
    assert "synthetic" not in json.dumps(report)


def test_conflicting_context_and_opaque_tools_are_excluded(tmp_path, monkeypatch):
    report, review = run_corpus(tmp_path, monkeypatch, {
        "a.jsonl": [call(), call(seq=1, tool="exec", inp={"input": "opaque"}, second=1)],
        "b.jsonl": [call(cwd="/different"), call(seq=2, tool="Edit", inp={"patch": "x"})],
        "empty.jsonl": [],
    })
    data = report["sources"]["claude_code"]
    assert data["excluded_calls"] == {
        "ambiguous-cwd": 1, "unsupported-tool": 1, "missing-write-path": 1,
    }
    assert data["flag_rate"] is None and not review
    assert data["counts"]["files_without_calls"] == 1


def test_missing_shell_command_is_not_green():
    assert study.exclusion(call(inp={"other": "missing"})) == "missing-command"


def test_private_context_is_redacted_and_only_precedes_action(tmp_path):
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps({
        "type": "user", "timestamp": ts, "message": {"content": text},
    }) for ts, text in [("2026-01-01T00:00:00Z", "Inspect sk-abcdefghijklmnopqrstuvwx"),
                       ("2026-01-01T00:00:02Z", "later permission")]))
    rows = [{"transcript": str(path), "timestamp": "2026-01-01T00:00:01+00:00"}]
    study.enrich_review(rows)
    assert "sk-abcdefghijklmnopqrstuvwx" not in str(rows)
    assert "later permission" not in str(rows)
    assert len(rows[0]["raw_user_context_before"]) == 1
