"""A negative claim is backed by the repository, never by our own silence.

`rule_did_not_touch` used to return CONFIRMED with method="rule" and an empty evidence list when
no edit events existed AND the repo state could not be read. That is the exact shape
`verdicts._enforce` forbids -- a confirmation with nothing a reader could check -- and it crashed
collection the moment a real session produced one (5f8a60d1, where the "path" was a mis-extracted
pytest flag, `-o python_files=<name>`).
"""
from __future__ import annotations

import pathlib
import subprocess

from custos_code.models import Claim, ClaimType, Verdict
from custos_code.rules import check
from custos_code.verdicts import run


def _claim(text: str, objs: list[str]) -> Claim:
    return Claim(id="c1", session_id="s", text=text, type=ClaimType.DID_NOT_TOUCH,
                 objects=objs, polarity="did_not")


def test_no_repo_means_unwitnessed_not_confirmed() -> None:
    c = _claim("I did not touch src/cart.py", ["src/cart.py"])
    rec = check(c, [], repo_root=None)
    assert rec is not None
    assert rec.verdict == Verdict.UNWITNESSED, rec.rationale
    assert "could not be read" in rec.rationale


def test_the_result_survives_enforce() -> None:
    """The regression: verdicts.run must not raise on this claim shape."""
    c = _claim("I did not touch `-o python_files=<name>`", ["-o python_files=<name>"])
    recs = run([c], [], repo_root=None)
    assert len(recs) == 1
    assert recs[0].verdict != Verdict.CONFIRMED


def test_a_real_clean_repo_still_confirms(tmp_path: pathlib.Path) -> None:
    """With a repo to check, an unchanged file is a genuine `state` confirmation."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    f = tmp_path / "src"
    f.mkdir()
    (f / "cart.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
                   cwd=tmp_path, check=True)
    c = _claim("I did not touch src/cart.py", ["src/cart.py"])
    rec = check(c, [], repo_root=str(tmp_path))
    assert rec is not None
    assert rec.verdict == Verdict.CONFIRMED
    assert rec.method == "state", "an evidence-free confirmation must be a state check"
    recs = run([c], [], repo_root=str(tmp_path))       # must not raise
    assert recs[0].verdict == Verdict.CONFIRMED
