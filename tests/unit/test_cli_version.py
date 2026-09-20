"""`custos-code --version` -- previously not a real option at all (Typer's default `Error: No
such option` for anyone who typed the flag everyone expects a CLI to have).
"""
from __future__ import annotations

from importlib.metadata import version

from typer.testing import CliRunner

from custos_code.cli import app

runner = CliRunner()


def test_version_flag_prints_the_installed_package_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert version("custos-code") in result.output


def test_short_version_flag_is_the_same_option() -> None:
    result = runner.invoke(app, ["-V"])
    assert result.exit_code == 0
    assert version("custos-code") in result.output


def test_version_is_eager_and_does_not_require_a_subcommand() -> None:
    """`--version` must exit before Typer complains about a missing command."""
    result = runner.invoke(app, ["--version"])
    assert "Missing command" not in result.output


def test_other_commands_are_unaffected_by_the_new_callback() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "check" in result.output and "watch" in result.output
