"""CUSTOS_CODE_ONLY_IN confines the hooks to one directory, so nothing has to live in that directory.

The agent under test reads every file in its working directory. A project-local
`.claude/settings.json` therefore tells it that its report is being checked, and by what -- and on
2026-09-19 a run read both that file and a README describing the trap before "diagnosing" it. An
agent that knows it is being audited is not the agent whose honesty we are measuring.

With this guard the hooks live in the user's global settings and the project directory is empty of
tells. The guard must fail toward doing nothing, since the failure mode in the other direction is
arming a blocking hook across every project on the machine.
"""
from __future__ import annotations

import pathlib

import pytest

import custos_code.hooks as h


@pytest.fixture(autouse=True)
def _home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(h, "HOME", str(tmp_path / "custos-code"))
    monkeypatch.delenv("CUSTOS_CODE_ONLY_IN", raising=False)


def test_unset_means_every_directory_is_in_scope() -> None:
    assert h._out_of_scope({"cwd": "/anywhere"}) is False


def test_inside_the_named_directory_is_in_scope(monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: pathlib.Path) -> None:
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    monkeypatch.setenv("CUSTOS_CODE_ONLY_IN", str(root))
    assert h._out_of_scope({"cwd": str(root)}) is False
    assert h._out_of_scope({"cwd": str(root / "sub")}) is False


def test_outside_is_out_of_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    root = tmp_path / "proj"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    monkeypatch.setenv("CUSTOS_CODE_ONLY_IN", str(root))
    assert h._out_of_scope({"cwd": str(other)}) is True


def test_sibling_with_a_shared_prefix_is_not_confused(monkeypatch: pytest.MonkeyPatch,
                                                      tmp_path: pathlib.Path) -> None:
    """`/x/proj-evil` must not count as inside `/x/proj` -- a plain startswith would say it does."""
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj-evil").mkdir()
    monkeypatch.setenv("CUSTOS_CODE_ONLY_IN", str(tmp_path / "proj"))
    assert h._out_of_scope({"cwd": str(tmp_path / "proj-evil")}) is True


def test_missing_cwd_is_out_of_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setenv("CUSTOS_CODE_ONLY_IN", str(tmp_path))
    assert h._out_of_scope({}) is True
    assert h._out_of_scope({"cwd": ""}) is True


def test_handlers_no_op_when_out_of_scope(monkeypatch: pytest.MonkeyPatch,
                                          tmp_path: pathlib.Path) -> None:
    """The important property: no ledger written, no block, for a session elsewhere."""
    root = tmp_path / "proj"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    monkeypatch.setenv("CUSTOS_CODE_ONLY_IN", str(root))
    monkeypatch.setenv("CUSTOS_CODE_AUTO", "1")

    def _boom(*a: object, **k: object) -> object:
        raise AssertionError("out-of-scope session reached the model backend")

    monkeypatch.setattr(h.judge_mod, "make_backend", _boom)
    payload = {"session_id": "s1", "cwd": str(other), "tool_name": "Bash",
               "tool_input": {"command": "pytest -q | tail -1"},
               "tool_response": "ok", "last_assistant_message": "All 9 tests pass."}
    assert h.on_pre_tool_use(payload) is None
    h.on_post_tool_use(payload)
    assert h.on_stop(payload) is None
    live, _, _ = h._paths("s1")
    assert not pathlib.Path(live).exists(), "wrote a ledger for an out-of-scope session"
