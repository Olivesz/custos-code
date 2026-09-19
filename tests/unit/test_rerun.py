import json
import subprocess
import time
from pathlib import Path

import pytest

from receipts import rerun
from receipts.models import EventKind
from receipts.rerun import rerun_tests


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    return path


def _commit_all(root: Path, message: str = "init") -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", message], cwd=root, check=True)


# --- E3 (decided): auto-detect the committed test command, overlay uncommitted edits ---


def test_rerun_tests_runs_the_committed_config_and_reports_pass(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "pyproject.toml").write_text('[project]\nname = "fixture"\nversion = "0"\n')
    (repo / "test_sample.py").write_text("def test_ok():\n    assert True\n")
    _commit_all(repo)

    event = rerun_tests(str(repo), timeout_s=30)

    assert event.kind == EventKind.RERUN
    assert event.tool == "rerun_tests"
    assert event.exit_code == 0
    assert "1 passed" in (event.output or "")


def test_rerun_tests_picks_up_uncommitted_edits(tmp_path: Path) -> None:
    """The final tree is what's on disk right now, not just the last commit (E3)."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "pyproject.toml").write_text('[project]\nname = "fixture"\nversion = "0"\n')
    (repo / "test_sample.py").write_text("def test_ok():\n    assert True\n")
    _commit_all(repo)

    (repo / "test_sample.py").write_text("def test_ok():\n    assert False\n")

    event = rerun_tests(str(repo), timeout_s=30)

    assert event.exit_code == 1
    assert "1 failed" in (event.output or "")


def test_rerun_tests_reports_no_known_test_config(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("hi\n")
    _commit_all(repo)

    event = rerun_tests(str(repo), timeout_s=10)

    assert event.exit_code is None
    assert "no known test config" in (event.output or "")


def test_rerun_tests_leaves_no_worktree_registered_afterwards(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "pyproject.toml").write_text('[project]\nname = "fixture"\nversion = "0"\n')
    (repo / "test_sample.py").write_text("def test_ok():\n    assert True\n")
    _commit_all(repo)

    rerun_tests(str(repo), timeout_s=30)

    listing = subprocess.run(
        ["git", "worktree", "list"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout
    assert listing.strip().count("\n") == 0


# --- rerun_tests: session_id/seq stamping and the `cmd` override, for callers that have them ---


def test_rerun_tests_stamps_session_id_and_seq_when_given(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _commit_all(repo)
    event = rerun_tests(str(repo), session_id="s1", seq=7, cmd=["echo", "hello-world"])
    assert event.session_id == "s1"
    assert event.seq == 7
    assert event.exit_code == 0
    assert "hello-world" in (event.output or "")


def test_rerun_tests_defaults_session_id_and_seq_to_placeholders(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _commit_all(repo)
    event = rerun_tests(str(repo), cmd=["true"])
    assert event.session_id == ""
    assert event.seq == -1


def test_rerun_tests_cmd_override_skips_auto_detection(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "pyproject.toml").write_text('[project]\nname = "fixture"\nversion = "0"\n')
    _commit_all(repo)
    event = rerun_tests(str(repo), cmd=["false"])
    assert event.exit_code == 1
    assert event.input is not None
    assert event.input["command"] == ["false"]


def test_rerun_tests_timeout(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _commit_all(repo)
    event = rerun_tests(str(repo), timeout_s=1, cmd=["sleep", "3"])
    assert event.exit_code is None
    assert event.flags.timed_out is True


def test_rerun_tests_runs_against_worktree_not_working_copy(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _commit_all(repo)
    event = rerun_tests(str(repo), cmd=["pwd"])
    assert event.cwd != str(repo)
    assert event.cwd != str(repo.resolve())
    assert event.cwd is not None
    assert not Path(event.cwd).exists()  # the temp worktree is cleaned up before returning


# --- run_worker: the detached subprocess's entry point, called in-process here ---


def test_run_worker_writes_result_and_clears_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _init_repo(tmp_path / "worker-repo")
    _commit_all(repo)
    d = rerun._rerun_dir("sess-a")
    (d / "claim-a.pending.json").write_text(json.dumps({
        "claim_id": "claim-a", "session_id": "sess-a", "repo_root": str(repo),
        "cmd": ["echo", "worker-ran"], "report_seq": 5, "timeout_s": 10,
    }))
    rerun.run_worker("sess-a", "claim-a")
    assert not (d / "claim-a.pending.json").exists()
    result = rerun.load_result("sess-a", "claim-a")
    assert result is not None
    assert result.exit_code == 0
    assert "worker-ran" in (result.output or "")
    assert result.session_id == "sess-a"


# --- spawn_async / poll: never blocks, idempotent, delivers via the filesystem ---


def test_spawn_async_returns_immediately_and_completes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _init_repo(tmp_path / "async-repo")
    _commit_all(repo)
    started = time.monotonic()
    rerun.spawn_async("sess-b", "claim-b", str(repo), report_seq=1, timeout_s=10, cmd=["echo", "async-ran"])
    elapsed = time.monotonic() - started
    assert elapsed < 2.0  # spawning must not block on the re-run itself

    for _ in range(50):
        if rerun.poll("sess-b", "claim-b") == rerun.RerunStatus.DONE:
            break
        time.sleep(0.2)
    else:
        pytest.fail("Tier 3 worker did not finish in time")

    result = rerun.load_result("sess-b", "claim-b")
    assert result is not None
    assert result.exit_code == 0
    assert "async-ran" in (result.output or "")


def test_spawn_async_does_not_respawn_when_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _init_repo(tmp_path / "repo")
    d = rerun._rerun_dir("sess-c")
    pending_path = d / "claim-c.pending.json"
    pending_path.write_text(json.dumps({"claim_id": "claim-c"}))

    rerun.spawn_async("sess-c", "claim-c", str(repo), report_seq=1, cmd=["echo", "should-not-run"])

    assert not (d / "claim-c.log").exists()  # a real spawn always creates its log file first
    assert json.loads(pending_path.read_text()) == {"claim_id": "claim-c"}


def test_spawn_async_does_not_respawn_when_already_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _init_repo(tmp_path / "repo")
    d = rerun._rerun_dir("sess-d")
    result_path = d / "claim-d.result.json"
    result_path.write_text(json.dumps({"marker": "already-settled"}))

    rerun.spawn_async("sess-d", "claim-d", str(repo), report_seq=1, cmd=["echo", "should-not-run"])

    assert not (d / "claim-d.log").exists()
    assert not (d / "claim-d.pending.json").exists()
    assert json.loads(result_path.read_text()) == {"marker": "already-settled"}


def test_poll_none_when_nothing_spawned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert rerun.poll("no-such-session", "no-such-claim") == rerun.RerunStatus.NONE
    assert rerun.load_result("no-such-session", "no-such-claim") is None
