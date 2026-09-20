"""The architecture watchdog at PreToolUse.

Two properties matter more than the detection itself.

It never denies. A diagram is a claim made by someone who is not in this session and may be out of
date; the strongest honest response to "the docs do not connect these" is to ask a human, not to
refuse. Denying on a stale diagram would make the checker the thing that blocks correct work.

It never raises. This runs in a hook, in a fresh process, before somebody's tool call. A
documentation parser that throws is a documentation parser that stops people working, and every
error path here has to end in "allow".
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from custos_code import hooks

DIAGRAM = """```mermaid
flowchart LR
  AD[Adapters] --> L[Ledger]
  BILL[Billing]
```
"""


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    monkeypatch.setattr(hooks, "HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "live").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "DESIGN.md").write_text(DIAGRAM, encoding="utf-8")
    for rel in ("adapters/__init__.py", "ledger.py", "billing/__init__.py"):
        p = tmp_path / "src" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")
    from custos_code import arch
    arch.load.cache_clear()
    return tmp_path


def _seed(repo: pathlib.Path, sid: str, *paths: str) -> None:
    """Put earlier writes in the live ledger, which is the only state a hook process can see."""
    live, _, _ = hooks._paths(sid)
    pathlib.Path(live).parent.mkdir(parents=True, exist_ok=True)
    with open(live, "w", encoding="utf-8") as fh:
        for i, p in enumerate(paths):
            fh.write(json.dumps({"seq": i, "kind": "call", "tool": "Write", "paths": [p]}) + "\n")


def _write(repo: pathlib.Path, target: str, sid: str = "s") -> dict[str, Any]:
    return {"session_id": sid, "cwd": str(repo), "tool_name": "Write",
            "tool_input": {"file_path": str(repo / target)}}


def test_a_crossing_asks_rather_than_denies(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    _seed(repo, "s", "src/adapters/__init__.py")
    out = hooks._arch_gate(_write(repo, "src/billing/__init__.py"))
    assert out is not None
    decision = out["hookSpecificOutput"]["permissionDecision"]
    assert decision == "ask", f"a stale diagram must not block correct work (got {decision})"
    assert "Billing" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_declared_edge_is_allowed_silently(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    _seed(repo, "s", "src/adapters/__init__.py")
    assert hooks._arch_gate(_write(repo, "src/ledger.py")) is None


def test_warn_mode_records_but_never_interrupts(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "warn")
    _seed(repo, "s", "src/adapters/__init__.py")
    assert hooks._arch_gate(_write(repo, "src/billing/__init__.py")) is None


def test_off_is_off(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "off")
    _seed(repo, "s", "src/adapters/__init__.py")
    assert hooks._arch_gate(_write(repo, "src/billing/__init__.py")) is None


def test_a_repo_without_a_diagram_is_never_gated(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Most repos have no flowchart. This check must then be inert, not invent boundaries."""
    monkeypatch.setattr(hooks, "HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    from custos_code import arch
    arch.load.cache_clear()
    assert hooks._arch_gate(_write(tmp_path, "src/a.py")) is None


def test_the_first_write_of_a_session_cannot_cross(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing has been touched yet, so there is no second component to be inconsistent with."""
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    assert hooks._arch_gate(_write(repo, "src/billing/__init__.py", sid="fresh")) is None


def test_a_parser_failure_allows_the_call(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The property the whole file exists for."""
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")

    def boom(_root: str) -> object:
        raise RuntimeError("diagram exploded")

    monkeypatch.setattr(hooks.arch_mod, "load", boom)
    _seed(repo, "s", "src/adapters/__init__.py")
    assert hooks._arch_gate(_write(repo, "src/billing/__init__.py")) is None


def test_non_write_tools_are_not_the_architecture_check_s_business(
        repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    _seed(repo, "s", "src/adapters/__init__.py")
    payload = _write(repo, "src/billing/__init__.py") | {"tool_name": "Read"}
    assert hooks._arch_gate(payload) is None
