"""`custos-code scope "<cmd>"` — band one command without running it.

The bands only mean something to someone who can check them against their own judgement on their
own commands. `scan` answers this for a recorded session, which is the wrong grain for the
question people actually ask before switching the gate on: would this have stopped me typing that.
"""
from __future__ import annotations

import pathlib

import pytest
from typer.testing import CliRunner

from custos_code.cli import app

CASES = [
    ("cat a.py | tee /Users/x/.zshrc", "YELLOW"),   # the laundering case
    ("ls > /Users/x/.zshrc", "YELLOW"),             # a redirect is a write
    ("cat ~/.ssh/config 2>/dev/null", "GREEN"),     # 2> is not
    ("git push --force origin main", "RED"),
    ("rm -rf /tmp/scratch/build", "GREEN"),
    ("pytest -q", "GREEN"),
]


@pytest.mark.parametrize("command,band", CASES, ids=[c[0][:26] for c in CASES])
def test_scope_reports_the_band_and_the_rule(command: str, band: str, tmp_path: pathlib.Path) -> None:
    res = CliRunner().invoke(app, ["scope", command, "--repo", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert band in res.output, res.output
    assert "rule" in res.output


def test_it_never_runs_the_command(tmp_path: pathlib.Path) -> None:
    """The obvious way to get this wrong. Banding `rm -rf` must not be how you lose the file."""
    canary = tmp_path / "canary.txt"
    canary.write_text("intact", encoding="utf-8")
    res = CliRunner().invoke(app, ["scope", f"rm -rf {canary}", "--repo", str(tmp_path)])
    assert res.exit_code == 0
    assert canary.exists() and canary.read_text(encoding="utf-8") == "intact"
