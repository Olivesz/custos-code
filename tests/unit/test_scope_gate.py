"""The PreToolUse scope gate. docs/SCOPE.md §5.

Two properties matter more than the banding itself, which test_scope.py covers:

1. It ships OFF. Every threshold in scope.py is a default I wrote, not a measurement. Until #57
   reports a false-positive rate against ~400 sessions of accepted work, a gate that interrupts
   good work is strictly worse than no gate -- the Stop-hook latency on 2026-09-19 is the lesson.
2. It fails OPEN. A checker that cannot run is not evidence about the agent, and the failure mode
   in the other direction is an agent that cannot use its own tools.
"""
from __future__ import annotations

import pathlib
import subprocess
from typing import Any

import pytest

import custos_code.hooks as h


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    monkeypatch.setattr(h, "HOME", str(tmp_path / ".receipts"))
    monkeypatch.delenv("CUSTOS_CODE_ONLY_IN", raising=False)
    monkeypatch.delenv("CUSTOS_CODE_AUTO", raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    return proj


def _call(repo: pathlib.Path, tool: str, inp: dict[str, Any]) -> dict[str, Any] | None:
    return h.on_pre_tool_use({"session_id": "s", "cwd": str(repo), "tool_name": tool,
                              "tool_use_id": "t1", "tool_input": inp})


def _decision(out: dict[str, Any] | None) -> str | None:
    if not out:
        return None
    return out.get("hookSpecificOutput", {}).get("permissionDecision")


# --- ships inert --------------------------------------------------------------------------------

def test_off_by_default_even_for_rm_rf(repo: pathlib.Path) -> None:
    """Uncalibrated thresholds must not be able to interrupt anyone."""
    assert _decision(_call(repo, "Bash", {"command": "rm -rf /Users/someone/Projects"})) is None


def test_warn_mode_bands_but_never_denies(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The mode #57's calibration harness runs in: measure without blocking."""
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "warn")
    assert _decision(_call(repo, "Bash", {"command": "rm -rf /Users/someone/Projects"})) is None


def test_an_unknown_mode_is_treated_as_off(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "yes-please")
    assert _decision(_call(repo, "Bash", {"command": "rm -rf /Users/someone/Projects"})) is None


# --- on ------------------------------------------------------------------------------------------

def test_red_denies(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    out = _call(repo, "Bash", {"command": "git push --force origin main"})
    assert _decision(out) == "deny"
    assert "git-force-push" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_yellow_asks_when_a_human_is_present(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    assert _decision(_call(repo, "Write", {"file_path": "/Users/someone/.zshrc"})) == "ask"


def test_yellow_denies_when_nobody_is_watching(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SCOPE.md §5: a flag is a message to a human. Unattended, 'ask' and 'do nothing' are equal."""
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    monkeypatch.setenv("CUSTOS_CODE_AUTO", "1")
    assert _decision(_call(repo, "Write", {"file_path": "/Users/someone/.zshrc"})) == "deny"


def test_green_passes_through_to_the_e5_rewrite(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate must not swallow the runner-resolution wrap it sits in front of."""
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    out = _call(repo, "Bash", {"command": "pytest -q"})
    assert _decision(out) is None
    assert out is not None and "updatedInput" in out["hookSpecificOutput"]


def test_the_granted_directory_is_never_gated(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    assert _decision(_call(repo, "Write", {"file_path": str(repo / "src" / "x.py")})) is None


# --- safety ---------------------------------------------------------------------------------------

def test_fails_open_when_classify_raises(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")

    def boom(*a: object, **k: object) -> object:
        raise RuntimeError("scope exploded")

    monkeypatch.setattr(h.scope_mod, "classify", boom)
    assert _decision(_call(repo, "Bash", {"command": "git push --force"})) is None


def test_out_of_scope_sessions_are_not_gated(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CUSTOS_CODE_ONLY_IN fences the whole hook; scope must not slip past it."""
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    monkeypatch.setenv("CUSTOS_CODE_ONLY_IN", str(repo / "elsewhere"))
    assert _decision(_call(repo, "Bash", {"command": "git push --force"})) is None


def test_approved_paths_stop_being_asked_about(repo: pathlib.Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Ratchet: a gate that re-asks is a gate people disable.

    The target is deliberately NOT under tmp_path. pytest puts its fixtures inside a scratch root
    ($TMPDIR on macOS, /tmp on Linux), so a tmp_path sibling is legitimately GREEN as disposable
    scratch and never reaches the ask -- the test would pass without exercising the ratchet at all.
    """
    import json
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    other = "/Users/someone-else/notes"
    target = f"{other}/x.py"
    assert _decision(_call(repo, "Write", {"file_path": target})) == "ask"
    _, state_p, _ = h._paths("s")
    with open(state_p, "w", encoding="utf-8") as fh:
        json.dump({"scope_approved": [other]}, fh)
    assert _decision(_call(repo, "Write", {"file_path": target})) is None
