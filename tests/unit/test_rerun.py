import subprocess
from pathlib import Path

from receipts.models import EventKind
from receipts.rerun import rerun_tests


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def _commit_all(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def test_rerun_tests_runs_the_committed_config_and_reports_pass(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "fixture"\nversion = "0"\n')
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert True\n")
    _commit_all(tmp_path)

    event = rerun_tests(str(tmp_path), timeout_s=30)

    assert event.kind == EventKind.RERUN
    assert event.tool == "rerun_tests"
    assert event.exit_code == 0
    assert "1 passed" in (event.output or "")


def test_rerun_tests_picks_up_uncommitted_edits(tmp_path: Path) -> None:
    """The final tree is what's on disk right now, not just the last commit (E3)."""
    _init_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "fixture"\nversion = "0"\n')
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert True\n")
    _commit_all(tmp_path)

    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert False\n")

    event = rerun_tests(str(tmp_path), timeout_s=30)

    assert event.exit_code == 1
    assert "1 failed" in (event.output or "")


def test_rerun_tests_reports_no_known_test_config(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "README.md").write_text("hi\n")
    _commit_all(tmp_path)

    event = rerun_tests(str(tmp_path), timeout_s=10)

    assert event.exit_code is None
    assert "no known test config" in (event.output or "")


def test_rerun_tests_leaves_no_worktree_registered_afterwards(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "fixture"\nversion = "0"\n')
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert True\n")
    _commit_all(tmp_path)

    rerun_tests(str(tmp_path), timeout_s=30)

    listing = subprocess.run(
        ["git", "worktree", "list"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout
    assert listing.strip().count("\n") == 0
