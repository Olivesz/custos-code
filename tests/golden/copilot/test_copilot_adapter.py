"""Copilot class R: the PR body is the report, and a missing log is stated, not papered over."""

import json
import os

import pytest

from custos_code.adapters import copilot
from custos_code.ledger import verify_chain
from custos_code.models import EventKind

BUNDLE = os.path.join(os.path.dirname(__file__), "bundle.json")


def test_bundle_parses_with_log_commits_and_checks() -> None:
    sess, ledger, report = copilot.parse(BUNDLE)
    assert sess.source == "copilot" and sess.id == "copilot-8821"
    assert sess.cwd == "/workspaces/app" and sess.git_branch == "copilot/cache-settings"
    # a caller-assembled bundle has no harness signature: class R never reaches 1.0
    assert sess.integrity_score == 0.8
    assert verify_chain(ledger)
    assert report is not None and "Caches `load_settings()`" in report
    assert not [e for e in ledger if (e.input or {}).get("event") == "no_tool_log"]


def test_tool_names_normalise_and_results_pair_with_their_call() -> None:
    _, ledger, _ = copilot.parse(BUNDLE)
    pairs = [(e.kind, e.tool, e.exit_code) for e in ledger if e.tool]
    assert pairs == [
        (EventKind.CALL, "Edit", None),
        (EventKind.RESULT, "Edit", 0),
        (EventKind.CALL, "Bash", None),
        (EventKind.RESULT, "Bash", 0),
        (EventKind.CALL, "Bash", None),
        (EventKind.RESULT, "Bash", 2),
        (EventKind.RESULT, "Git", 0),
        (EventKind.CALL, "CI", None),
        (EventKind.RESULT, "CI", 0),
    ]
    edit = next(e for e in ledger if e.tool == "Edit" and e.kind is EventKind.RESULT)
    assert edit.paths == ["src/app/settings.py"]


def test_piped_test_run_is_flagged_and_failed_build_is_an_error() -> None:
    _, ledger, _ = copilot.parse(BUNDLE)
    tests, build = (e for e in ledger if e.tool == "Bash" and e.kind is EventKind.RESULT)
    assert tests.flags.piped  # "the full suite passed" is unrecorded when the output was filtered
    assert build.exit_code == 2 and build.flags.error


def test_absent_log_is_recorded_as_a_known_gap(tmp_path) -> None:
    with open(BUNDLE) as fh:
        bundle = json.load(fh)
    bundle.pop("log")
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle))
    sess, ledger, _ = copilot.parse(str(path))
    gap = [e for e in ledger if (e.input or {}).get("event") == "no_tool_log"]
    assert len(gap) == 1 and (gap[0].input or {})["class"] == "R"
    assert sess.integrity_score == 0.5
    assert not [e for e in ledger if e.tool == "Bash"]


def test_find_last_session(tmp_path) -> None:
    old, new = tmp_path / "a.json", tmp_path / "b.json"
    old.write_text("{}")
    new.write_text("{}")
    os.utime(old, (1, 1))
    assert copilot.find_last_session(str(tmp_path)).endswith("b.json")
    with pytest.raises(FileNotFoundError):
        copilot.find_last_session(str(tmp_path / "nope"))
