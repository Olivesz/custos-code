"""E12: `receipts cost --path review|ladder|judge-all`. No live API key needed -- every judge
call goes through a stub backend that mimics the OpenAI Responses shape review.py/judge.py read.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from typer.testing import CliRunner

from receipts import compress as compress_mod
from receipts import judge as judge_mod
from receipts.cli import app
from receipts.models import Claim, Verdict, VerdictRecord

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "golden", "claude_code", "session.jsonl")
runner = CliRunner()


def _write_escalating_session(tmp_path: Path) -> str:
    """A session whose sole claim ('observed_output' with no numeric/path needle) has no rule
    that can settle it (rule_observed_output returns None with nothing to grep for) -- the one
    reliable way to make verdicts.run escalate a claim to the judge without an unwitnessed-tier-4
    fallback already covering the case."""
    sid = "22222222-3333-4444-5555-666666666666"
    lines = [
        {"parentUuid": None, "isSidechain": False, "cwd": "/home/dev/proj", "sessionId": sid,
         "gitBranch": "main", "type": "user", "uuid": "u1", "timestamp": "2026-09-19T10:00:00.000Z",
         "version": "2.1.0", "message": {"role": "user", "content": [{"type": "text", "text": "fix it"}]}},
        {"parentUuid": "u1", "isSidechain": False, "cwd": "/home/dev/proj", "sessionId": sid,
         "gitBranch": "main", "type": "assistant", "uuid": "u2", "timestamp": "2026-09-19T10:00:05.000Z",
         "version": "2.1.0",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "The output now shows correctly."}]}},
    ]
    path = tmp_path / "escalating.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return str(path)


class _StubResponses:
    def __init__(self, claims_json: list[dict[str, Any]]) -> None:
        self._claims = claims_json

    def create(self, **kw: Any) -> Any:
        return SimpleNamespace(
            output_text=json.dumps({"claims": self._claims}),
            usage=SimpleNamespace(input_tokens=111, output_tokens=22),
        )


class _StubClient:
    def __init__(self, claims_json: list[dict[str, Any]]) -> None:
        self.responses = _StubResponses(claims_json)


class _StubBackend:
    """Same shape review.review()/verdicts.run() read: .client(), .judge_model, .judge(), .usage."""

    judge_model = "stub-judge"

    def __init__(self, claims_json: list[dict[str, Any]] | None = None) -> None:
        self._claims_json = claims_json or [
            {"claim": "ran the suite, all 5 passing", "verdict": "confirmed", "evidence": [1], "reason": "stub"}
        ]
        self.usage = judge_mod.Usage(requests=1, input_tokens=50, output_tokens=10, model=self.judge_model)

    def client(self) -> _StubClient:
        return _StubClient(self._claims_json)

    def judge(self, claims: list[Claim], window: list[Any]) -> list[VerdictRecord]:
        return [
            VerdictRecord(claim_id=c.id, verdict=Verdict.CONFIRMED, tier=4, method="judge",
                          confidence=0.9, evidence=[window[0].seq] if window else [], rationale="stub judge-all")
            for c in claims
        ]


def test_path_ladder_needs_no_backend_and_reports_rules(monkeypatch: Any) -> None:
    monkeypatch.setattr(judge_mod, "make_backend", lambda *a, **k: None)
    result = runner.invoke(app, ["cost", FIXTURE, "--path", "ladder"])
    assert result.exit_code == 0, result.output
    assert "receipts cost" in result.output
    assert "TOTAL" in result.output


def test_path_review_uses_the_stub_backend_and_prices_tier_4(monkeypatch: Any) -> None:
    monkeypatch.setattr(judge_mod, "make_backend", lambda *a, **k: _StubBackend())
    result = runner.invoke(app, ["cost", FIXTURE, "--path", "review", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["judge"]["requests"] == 1
    assert data["judge"]["input_tokens"] == 111  # from the stub Responses.create call, not Usage()
    assert data["judge"]["model"] == "stub-judge"
    assert data["claims_by_method"].get("judge") == 1  # review.py always tags its records method="judge"


def test_path_judge_all_judges_every_extracted_claim(monkeypatch: Any) -> None:
    monkeypatch.setattr(judge_mod, "make_backend", lambda *a, **k: _StubBackend())
    result = runner.invoke(app, ["cost", FIXTURE, "--path", "judge-all", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["claims_total"] > 0
    assert data["claims_by_method"].get("judge") == data["claims_total"]  # nothing settled by rules


def test_no_backend_falls_back_to_ladder_for_review_and_judge_all(monkeypatch: Any) -> None:
    monkeypatch.setattr(judge_mod, "make_backend", lambda *a, **k: None)
    for path in ("review", "judge-all"):
        result = runner.invoke(app, ["cost", FIXTURE, "--path", path])
        assert result.exit_code == 0, result.output
        assert "falling back to the rules ladder" in result.output


def test_compress_on_review_path_is_a_documented_no_op(monkeypatch: Any) -> None:
    monkeypatch.setattr(judge_mod, "make_backend", lambda *a, **k: _StubBackend())
    result = runner.invoke(app, ["cost", FIXTURE, "--path", "review", "--compress"])
    assert result.exit_code == 0, result.output
    assert "--compress has no effect on --path review yet" in result.output


def test_compress_off_by_config_prints_a_warning_not_a_crash(monkeypatch: Any) -> None:
    monkeypatch.setattr(judge_mod, "make_backend", lambda *a, **k: None)
    result = runner.invoke(app, ["cost", FIXTURE, "--path", "ladder", "--compress"])
    assert result.exit_code == 0, result.output
    assert "compressor off" in result.output


class _StubCompressor:
    def __init__(self) -> None:
        self.usage = compress_mod.Usage()
        self.calls: list[list[Any]] = []

    def compress_window(self, window: list[Any]) -> str:
        self.calls.append(window)
        self.usage.add(compress_mod.Usage(requests=1, input_tokens=400, tokens_saved=150))
        return "compressed"


def test_compress_on_ladder_path_reports_a_real_measured_line(monkeypatch: Any, tmp_path: Path) -> None:
    """Only the escalated (judge-touched) claims get windowed and compressed, same scope as
    eval/cost_report.py's arm_ladder_compressed -- not the whole ledger."""
    session = _write_escalating_session(tmp_path)
    stub_compressor = _StubCompressor()
    monkeypatch.setattr(judge_mod, "make_backend", lambda *a, **k: _StubBackend())
    monkeypatch.setattr(compress_mod, "make_compressor", lambda *a, **k: stub_compressor)
    result = runner.invoke(app, ["cost", session, "--path", "ladder", "--compress", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["compress"]["enabled"] is True
    assert data["compress"]["input_tokens"] == 400
    assert data["compress"]["tokens_saved"] == 150
    assert len(stub_compressor.calls) == 1
