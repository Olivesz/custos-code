"""Devin Path C: state is evidence, Devin's own prose is not (docs/DEVIN.md, AGENTS.md invariant 1)."""

import json
import os

import pytest

from custos_code.adapters import devin
from custos_code.ledger import verify_chain
from custos_code.models import EventKind

BUNDLE = os.path.join(os.path.dirname(__file__), "bundle.json")


def test_bundle_parses_into_a_state_only_ledger() -> None:
    sess, ledger, report = devin.parse(BUNDLE)
    assert sess.source == "devin" and sess.id == "devin-3f9c11aa"
    assert sess.cwd == "/home/ubuntu/widgets" and sess.git_branch == "devin/retry-upload"
    assert sess.integrity_score == 0.5  # half the ladder is blind without a tool log
    assert verify_chain(ledger)
    assert {e.tool for e in ledger if e.tool} == {"Git", "CI", "Read"}
    assert report is not None
    assert (
        "Adds `retry()`" in report and "structured_output" in report and "Final message" in report
    )


def test_missing_tool_log_is_recorded_not_assumed() -> None:
    _, ledger, _ = devin.parse(BUNDLE)
    gap = [e for e in ledger if (e.input or {}).get("event") == "no_tool_log"]
    assert len(gap) == 1
    assert (gap[0].input or {})["class"] == "C"
    # no Bash anywhere: a "ran the tests locally" claim must land on `unrecorded`, not a witness
    assert not [e for e in ledger if e.tool == "Bash"]


def test_devin_messages_are_text_never_results() -> None:
    _, ledger, _ = devin.parse(BUNDLE)
    prose = [e for e in ledger if e.kind in (EventKind.TEXT, EventKind.USER)]
    assert [e.kind for e in prose] == [EventKind.USER, EventKind.TEXT]
    assert all(e.exit_code is None and e.tool is None for e in prose)


def test_commit_names_its_files_and_failed_ci_is_positive_evidence() -> None:
    _, ledger, _ = devin.parse(BUNDLE)
    commit = next(e for e in ledger if e.tool == "Git")
    assert commit.paths == [
        "/home/ubuntu/widgets/src/widgets/upload.py",
        "/home/ubuntu/widgets/tests/unit/test_upload.py",
    ]
    assert commit.exit_code == 0
    ci = next(e for e in ledger if e.tool == "CI" and e.kind == EventKind.RESULT)
    assert ci.exit_code == 1 and ci.flags.error and ci.duration_ms == 210_000
    assert "1 failed" in (ci.output or "")


def test_absent_file_probe_is_an_error_result() -> None:
    _, ledger, _ = devin.parse(BUNDLE)
    probes = {e.paths[0]: e for e in ledger if e.tool == "Read"}
    assert probes["/home/ubuntu/widgets/docs/upload.md"].exit_code == 1
    assert probes["/home/ubuntu/widgets/docs/upload.md"].flags.error
    assert probes["/home/ubuntu/widgets/src/widgets/upload.py"].exit_code == 0


def test_raw_get_session_response_is_accepted(tmp_path) -> None:
    with open(BUNDLE) as fh:
        raw = json.load(fh)["session"]
    path = tmp_path / "session.json"
    path.write_text(json.dumps(raw))
    sess, ledger, report = devin.parse(str(path))
    assert sess.id == "devin-3f9c11aa" and report is not None
    assert not [e for e in ledger if e.tool in ("Git", "CI")]


def test_api_helpers_require_a_key(monkeypatch) -> None:
    monkeypatch.delenv("DEVIN_API_KEY", raising=False)
    with pytest.raises(ValueError, match="no Devin API key"):
        devin.fetch_bundle("devin-1")
    with pytest.raises(ValueError, match="no Devin API key"):
        devin.nudge("devin-1", "claim 3 is contradicted")


def test_api_helpers_hit_the_documented_v1_endpoints(monkeypatch) -> None:
    seen: list[tuple[str, str, dict | None]] = []

    def fake(method: str, url: str, token: str, body: dict | None = None) -> dict:
        seen.append((method, url, body))
        return {"ok": True}

    monkeypatch.setattr(devin, "_api", fake)
    devin.fetch_bundle("devin-1", token="t")
    devin.nudge("devin-1", "fix claim 3", token="t")
    assert seen == [
        ("GET", "https://api.devin.ai/v1/sessions/devin-1", None),
        ("POST", "https://api.devin.ai/v1/sessions/devin-1/message", {"message": "fix claim 3"}),
    ]
