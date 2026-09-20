"""The watchdog: one place that decides whether to stop an agent before it acts.

Scope and architecture were two gates answering the same question. Consolidating them means the
attended/unattended rule, the fail-open path and the mode check exist once. These tests pin the
three properties that consolidation could plausibly have broken.

The fixture is a real git work tree on purpose. Without it every write is `unrecoverable-write`
and the scope detector fires on everything, which would hide whether the architecture detector
works at all.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
from typing import Any

import pytest

from custos_code import arch, hooks, watchdog

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
        f = tmp_path / "src" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x = 1\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    arch.load.cache_clear()
    return tmp_path


def _seed(sid: str, *paths: str) -> None:
    """Earlier writes, in the live ledger -- the only state a fresh hook process can see."""
    live, _, _ = hooks._paths(sid)
    pathlib.Path(live).parent.mkdir(parents=True, exist_ok=True)
    with open(live, "w", encoding="utf-8") as fh:
        for i, p in enumerate(paths):
            fh.write(json.dumps({"seq": i, "kind": "call", "tool": "Write", "paths": [p]}) + "\n")


def _write(repo: pathlib.Path, target: str, sid: str = "s") -> dict[str, Any]:
    return {"session_id": sid, "cwd": str(repo), "tool_name": "Write",
            "tool_input": {"file_path": str(repo / target)}}


# --- the gate ------------------------------------------------------------------------------

def test_a_documented_crossing_asks_and_never_denies(
        repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A diagram is a claim by someone not in this session and possibly out of date. Refusing on
    it would make the checker the thing that blocks correct work."""
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    _seed("s", "src/adapters/__init__.py")
    out = hooks._watchdog_gate(_write(repo, "src/billing/__init__.py"))
    assert out is not None
    got = out["hookSpecificOutput"]["permissionDecision"]
    assert got == "ask", f"architecture evidence cannot support a refusal (got {got})"


def test_a_crossing_is_detected_from_the_real_post_tool_use_path(
        repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`_seed` writes prior paths as relative strings by hand. A real Claude Code session never
    does that -- `tool_input.file_path` for Write/Edit is always absolute, and `on_post_tool_use`
    records it as given. Left un-normalised against `cwd`, an absolute prior path could never
    resolve to a component (`Architecture.component_for` only matches repo-relative paths), so
    `crossings()` never saw more than the current write and could never find a pair -- every real
    crossing was silently missed outside a test that fed it relative paths by hand.
    """
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    first = _write(repo, "src/adapters/__init__.py")
    first["tool_response"] = {"filePath": first["tool_input"]["file_path"]}
    hooks.on_post_tool_use(first)  # the real write path: records the absolute file_path as-is

    out = hooks._watchdog_gate(_write(repo, "src/billing/__init__.py"))

    assert out is not None, "a real prior absolute-path write must still be seen as a crossing"
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "Billing" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_declared_edge_passes_silently(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    _seed("s", "src/adapters/__init__.py")
    assert hooks._watchdog_gate(_write(repo, "src/ledger.py")) is None


def test_warn_records_but_never_interrupts(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "warn")
    _seed("s", "src/adapters/__init__.py")
    assert hooks._watchdog_gate(_write(repo, "src/billing/__init__.py")) is None


def test_off_is_off(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "off")
    _seed("s", "src/adapters/__init__.py")
    assert hooks._watchdog_gate(_write(repo, "src/billing/__init__.py")) is None


def test_a_repo_without_a_diagram_is_not_gated_by_architecture(
        tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Most repos have no flowchart. The detector must be inert, not invent boundaries."""
    monkeypatch.setattr(hooks, "HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    arch.load.cache_clear()
    out = hooks._watchdog_gate(_write(tmp_path, "src/a.py"))
    reason = out["hookSpecificOutput"]["permissionDecisionReason"] if out else ""
    assert "boundary" not in reason, "invented an architecture where none is documented"


def test_the_first_write_of_a_session_cannot_cross(
        repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    assert hooks._watchdog_gate(_write(repo, "src/billing/__init__.py", sid="fresh")) is None


def test_a_detector_that_raises_allows_the_call(
        repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The property the module exists for. A checker that cannot run is not evidence."""
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")

    def boom(_root: str) -> object:
        raise RuntimeError("diagram exploded")

    monkeypatch.setattr(watchdog.arch_mod, "load", boom)
    _seed("s", "src/adapters/__init__.py")
    assert hooks._watchdog_gate(_write(repo, "src/billing/__init__.py")) is None


def test_reads_are_not_the_architecture_detector_s_business(
        repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    _seed("s", "src/adapters/__init__.py")
    payload = _write(repo, "src/billing/__init__.py") | {"tool_name": "Read"}
    out = hooks._watchdog_gate(payload)
    assert out is None or "boundary" not in out["hookSpecificOutput"]["permissionDecisionReason"]


# --- the policy, tested directly -------------------------------------------------------------

def _ob(sev: str, may_deny: bool = True, rule: str = "r") -> watchdog.Observation:
    return watchdog.Observation(detector="d", severity=sev, rule=rule,  # type: ignore[arg-type]
                                detail="detail", may_deny=may_deny)


def test_nothing_observed_is_allow() -> None:
    assert watchdog.decide([], unattended=False).decision == "allow"


def test_worst_wins_and_keeps_its_own_words() -> None:
    """"Blocked by policy" teaches nobody anything, so the winning observation explains itself."""
    v = watchdog.decide([_ob("ask", rule="soft"), _ob("deny", rule="rm-recursive-force")],
                        unattended=False)
    assert v.decision == "deny" and "rm-recursive-force" in v.reason


def test_unattended_turns_an_ask_into_a_deny() -> None:
    """An "ask" with nobody reading it is a hang, not a safeguard."""
    assert watchdog.decide([_ob("ask")], unattended=True).decision == "deny"


def test_unattended_does_not_promote_a_detector_that_may_not_deny() -> None:
    """An unattended run does not make a stale diagram more authoritative."""
    assert watchdog.decide([_ob("ask", may_deny=False)], unattended=True).decision == "ask"


def test_a_detector_that_may_not_deny_is_capped_at_ask() -> None:
    assert watchdog.decide([_ob("deny", may_deny=False)], unattended=False).decision == "ask"


def test_an_unattended_run_still_only_asks_about_a_documented_crossing(
        repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The property the may_deny flag exists for, end to end.

    Unattended, an `ask` from a detector that is allowed to deny becomes a deny -- an "ask" with
    nobody reading it is a hang. The architecture detector is deliberately excluded from that
    promotion: a stale diagram must not become the thing that halts an unattended run, and nobody
    is there to overrule it.

    Written after a mutation test: flipping `may_deny` to True in `_arch_observation` passed the
    whole file, because every other test here runs attended.
    """
    monkeypatch.setenv("CUSTOS_CODE_SCOPE", "on")
    monkeypatch.setattr(hooks, "_config", lambda: {"auto": True})
    _seed("s", "src/adapters/__init__.py")
    out = hooks._watchdog_gate(_write(repo, "src/billing/__init__.py"))
    assert out is not None
    got = out["hookSpecificOutput"]["permissionDecision"]
    assert got == "ask", f"a diagram halted an unattended run (got {got})"
