"""`custos-code record --wrapper`: installs the PATH-first bash/sh wrapper (docs/ADAPTERS.md §4/§7)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from custos_code.adapters import machine
from custos_code.cli import app

runner = CliRunner()


def test_wrapper_flag_installs_scripts_without_touching_any_rc_file(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(machine, "WRAPPER_DIR", str(tmp_path / "bin"))
    result = runner.invoke(app, ["record", "--wrapper"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "bin" / "bash").exists()
    assert (tmp_path / "bin" / "sh").exists()
    assert "Prepend to PATH yourself" in result.output


def test_wrapper_and_install_together_write_the_path_line_into_the_rc_file(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setattr(machine, "WRAPPER_DIR", str(tmp_path / "bin"))
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["record", "--wrapper", "--install", "--shell", "bash"])
    assert result.exit_code == 0, result.output
    rc = (tmp_path / ".bashrc").read_text()
    assert "custos-code recorder (class M) wrapper" in rc
    assert str(tmp_path / "bin") in rc
    # re-running must not duplicate the block
    result2 = runner.invoke(app, ["record", "--wrapper", "--install", "--shell", "bash"])
    assert result2.exit_code == 0, result2.output
    assert (tmp_path / ".bashrc").read_text().count("custos-code recorder (class M) wrapper") == 2  # open+close marker


def test_wrapper_flag_fails_closed_when_no_real_shell_is_findable(monkeypatch: Any, tmp_path: Path) -> None:
    def _never_found(bin_dir: str | None = None, which: Any = None) -> dict[str, str]:
        raise FileNotFoundError("no real 'bash' found on PATH")

    monkeypatch.setattr(machine, "install_wrapper", _never_found)
    result = runner.invoke(app, ["record", "--wrapper"])
    assert result.exit_code == 1
    assert "no real 'bash' found" in result.output
