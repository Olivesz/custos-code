"""Build reruns must retain the claim binding and committed entry point."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from custos_code import rerun, verdicts
from custos_code.models import Claim, ClaimType, LedgerEvent, Verdict


def _repo(path: Path, name: str, content: str) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / name).write_text(content)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=T", "-c", "user.email=t@t",
                    "commit", "-qm", "init"], check=True)
    return path


def _claim() -> Claim:
    return Claim(id="c", session_id="s", text="The build is clean.", type=ClaimType.BUILD)


def _event(**updates: object) -> LedgerEvent:
    data = dict(seq=3, session_id="s", ts="2026-09-20T00:00:00Z", kind="rerun",
                tool="rerun_tests", input={"claim_id": "c", "claim_text": _claim().text,
                    "claim_kind": "build", "report_seq": 1, "command": ["make", "build"]},
                output="built", exit_code=0)
    data.update(updates)
    return LedgerEvent.model_validate(data)


@pytest.mark.parametrize("code,flags,want", [
    (0, {}, Verdict.CONFIRMED), (1, {}, Verdict.CONTRADICTED),
    (None, {}, Verdict.UNRECORDED), (127, {}, Verdict.UNRECORDED),
    (0, {"timed_out": True}, Verdict.UNRECORDED),
    (0, {"truncated": True}, Verdict.UNRECORDED),
    (0, {"interrupted": True}, Verdict.UNRECORDED),
])
def test_build_outcomes(code: int | None, flags: dict, want: Verdict) -> None:
    rec, = verdicts.run([_claim()], [_event(exit_code=code, flags=flags)], None)
    assert (rec.verdict, rec.tier, rec.method, rec.evidence) == (want, 3, "rerun", [3])


def test_test_result_does_not_confirm_build_and_vice_versa() -> None:
    e = _event()
    assert e.input is not None
    e.input["claim_kind"] = "run_tests"
    rec, = verdicts.run([_claim()], [e], None)
    assert rec.verdict == Verdict.UNWITNESSED
    c = _claim().model_copy(update={"type": ClaimType.RUN_TESTS})
    rec, = verdicts.run([c], [_event()], None)
    assert rec.verdict == Verdict.UNWITNESSED


@pytest.mark.parametrize("change", ["session", "claim", "text", "sidechain", "later", "unbound"])
def test_unrelated_and_stale_build_results_are_ignored(change: str) -> None:
    e = _event()
    led = [e]
    assert e.input is not None
    if change == "session":
        e.session_id = "other"
    elif change == "claim":
        e.input["claim_id"] = "other"
    elif change == "text":
        e.input["claim_text"] = "Another claim"
    elif change == "sidechain":
        e.flags.sidechain = True
    elif change == "unbound":
        e.input = {}
    else:
        led.append(e.model_copy(update={"seq": 4, "kind": "call", "tool": "Edit"}))
    rec, = verdicts.run([_claim()], led, None)
    assert rec.verdict == Verdict.UNWITNESSED


@pytest.mark.parametrize("content", ["[]", "null", "42", "{invalid"])
def test_invalid_package_configuration_is_not_a_build(tmp_path: Path, content: str) -> None:
    repo = _repo(tmp_path / "repo", "package.json", content)
    assert rerun.detect_command(str(repo), "build") is None


def test_phony_declaration_alone_is_not_a_build(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo", "Makefile", ".PHONY: build\n")
    assert rerun.detect_command(str(repo), "build") is None


def test_build_config_is_committed_but_source_edits_are_live(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo", "package.json", '{"scripts":{"build":"real-builder"}}')
    (repo / "package.json").write_text('{"scripts":{"build":"echo fake"}}')
    (repo / "source.txt").write_text("live edit")
    ev = rerun.rerun_tests(str(repo), claim_kind="build", cmd=[sys.executable, "-c",
        "from pathlib import Path; print(Path('package.json').read_text()); print(Path('source.txt').read_text())"])
    assert ev.exit_code == 0
    assert "real-builder" in (ev.output or "") and "live edit" in (ev.output or "")
    assert "fake" not in (ev.output or "")
    assert "fake" in (repo / "package.json").read_text()


def test_missing_build_binary_is_incomplete_not_a_failed_build(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo", "Makefile", "build:\n\ttrue\n")
    ev = rerun.rerun_tests(str(repo), claim_kind="build", cmd=[str(tmp_path / "missing")])
    assert ev.exit_code is None
    assert "Could not start runner" in (ev.output or "")


def test_lint_claim_does_not_trigger_a_generic_build(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo", "Makefile", "build:\n\ttrue\n")
    c = _claim().model_copy(update={"text": "Ruff passed."})
    assert verdicts.rerun_command(c, str(repo)) is None


def test_build_golden_cases() -> None:
    cases = json.loads((Path(__file__).parents[1] / "golden/build_reruns/cases.json").read_text())
    for case in cases:
        rec, = verdicts.run([_claim()], [_event(**case["event"])], None)
        assert rec.verdict.value == case["verdict"], case["name"]


def test_worker_collection_settles_the_build_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from custos_code import hooks

    repo = _repo(tmp_path / "repo", "Makefile", "build:\n\ttrue\n")
    monkeypatch.setattr(rerun, "_sessions_root", lambda: tmp_path / "sessions")
    d = rerun._rerun_dir("s")
    (d / "c.pending.json").write_text(json.dumps({
        "repo_root": str(repo), "report_seq": 1, "timeout_s": 10,
        "cmd": [sys.executable, "-c", "print('built')"],
        "claim_kind": "build", "claim_text": _claim().text,
    }))
    rerun.run_worker("s", "c")
    base = [_event(seq=1, kind="result")]
    ledger = hooks._collect_reruns("s", base, str(tmp_path / "absent"))
    result, = verdicts.run([_claim()], ledger, None)
    assert (result.verdict, result.tier, result.evidence) == (Verdict.CONFIRMED, 3, [2])


def test_missing_python_build_module_is_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path / "repo", "pyproject.toml", '[build-system]\nrequires = []\n')
    monkeypatch.setattr(rerun.importlib.util, "find_spec", lambda name: None)
    ev = rerun.rerun_tests(str(repo), claim_kind="build")
    assert ev.exit_code is None
    assert "not installed" in (ev.output or "")
