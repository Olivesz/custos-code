import json
import subprocess
import time
from pathlib import Path

import pytest

from receipts import rerun
from receipts.models import EventKind


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=path, check=True)
    return path


# --- rerun_tests: the synchronous Tier 3 re-execution ---


def test_rerun_tests_success(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    event = rerun.rerun_tests(str(repo), "echo hello-world", session_id="s1", seq=7)
    assert event.kind == EventKind.RERUN
    assert event.session_id == "s1"
    assert event.seq == 7
    assert event.exit_code == 0
    assert "hello-world" in (event.output or "")


def test_rerun_tests_failure_exit_code(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    event = rerun.rerun_tests(str(repo), "false", session_id="s1", seq=1)
    assert event.exit_code == 1


def test_rerun_tests_timeout(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    event = rerun.rerun_tests(str(repo), "sleep 3", session_id="s1", seq=1, timeout_s=1)
    assert event.exit_code is None
    assert event.input is not None
    assert event.input["timed_out"] is True


def test_rerun_tests_cleans_up_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    event = rerun.rerun_tests(str(repo), "true", session_id="s1", seq=1)
    worktree_dir = event.cwd
    assert worktree_dir is not None
    assert not Path(worktree_dir).exists()
    listing = subprocess.run(
        ["git", "worktree", "list"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout
    assert str(repo.resolve()) in listing or len(listing.strip().splitlines()) == 1
    assert worktree_dir not in listing


def test_rerun_tests_runs_against_worktree_not_working_copy(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    event = rerun.rerun_tests(str(repo), "pwd", session_id="s1", seq=1)
    assert event.cwd != str(repo)
    assert event.cwd != str(repo.resolve())


# --- run_worker: the detached subprocess's entry point, called in-process here ---


def test_run_worker_writes_result_and_clears_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _init_repo(tmp_path / "repo")
    d = rerun._rerun_dir("sess-a")
    (d / "claim-a.pending.json").write_text(json.dumps({
        "claim_id": "claim-a", "session_id": "sess-a", "repo_root": str(repo),
        "cmd": "echo worker-ran", "report_seq": 5, "timeout_s": 10,
    }))
    rerun.run_worker("sess-a", "claim-a")
    assert not (d / "claim-a.pending.json").exists()
    result = rerun.load_result("sess-a", "claim-a")
    assert result is not None
    assert result.exit_code == 0
    assert "worker-ran" in (result.output or "")


# --- spawn_async / poll: never blocks, idempotent, delivers via the filesystem ---


def test_spawn_async_returns_immediately_and_completes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _init_repo(tmp_path / "repo")
    started = time.monotonic()
    rerun.spawn_async("sess-b", "claim-b", str(repo), "echo async-ran", report_seq=1, timeout_s=10)
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

    rerun.spawn_async("sess-c", "claim-c", str(repo), "echo should-not-run", report_seq=1)

    assert not (d / "claim-c.log").exists()  # a real spawn always creates its log file first
    assert json.loads(pending_path.read_text()) == {"claim_id": "claim-c"}


def test_spawn_async_does_not_respawn_when_already_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _init_repo(tmp_path / "repo")
    d = rerun._rerun_dir("sess-d")
    result_path = d / "claim-d.result.json"
    result_path.write_text(json.dumps({"marker": "already-settled"}))

    rerun.spawn_async("sess-d", "claim-d", str(repo), "echo should-not-run", report_seq=1)

    assert not (d / "claim-d.log").exists()
    assert not (d / "claim-d.pending.json").exists()
    assert json.loads(result_path.read_text()) == {"marker": "already-settled"}


def test_poll_none_when_nothing_spawned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert rerun.poll("no-such-session", "no-such-claim") == rerun.RerunStatus.NONE
    assert rerun.load_result("no-such-session", "no-such-claim") is None
