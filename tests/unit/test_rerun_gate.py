"""When Tier 3 re-execution is worth launching. docs/SCOPE.md and verdicts.should_rerun.

`rerun.py` knew HOW to re-run safely and nothing decided WHEN, so nothing ever called it -- the
"second grounded source" the architecture argues for did not exist in the running product.

The risk in wiring it up is not a bad re-run; `rerun.py` already runs only the repo's COMMITTED
config, read from the git object store so the agent cannot steer it. The risk is a checker that
spends a minute re-running things that settle nothing. So every condition below is tested for the
case it PREVENTS, and the common case is that the gate says no.
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

from receipts.models import Claim, ClaimType, Verdict, VerdictRecord
from receipts.verdicts import RERUN_BUDGET_PER_SESSION, rerun_key, should_rerun


def _claim(t: ClaimType = ClaimType.RUN_TESTS, cid: str = "c1") -> Claim:
    return Claim(id=cid, session_id="s", text="all tests pass", type=t)


def _rec(v: Verdict = Verdict.UNWITNESSED) -> VerdictRecord:
    return VerdictRecord(claim_id="c1", verdict=v, tier=4, method="rule",
                         confidence=0.5, evidence=[], rationale="")


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
                   cwd=tmp_path, check=True)
    return tmp_path


def test_it_runs_when_a_rerun_could_settle_the_claim(repo: pathlib.Path) -> None:
    assert should_rerun(_claim(), _rec(), str(repo)) is True


@pytest.mark.parametrize("verdict", [Verdict.CONFIRMED, Verdict.CONTRADICTED, Verdict.QUALIFIED])
def test_a_settled_verdict_is_never_re_run(verdict: Verdict, repo: pathlib.Path) -> None:
    """Re-running a settled claim buys nothing: a disagreement would be a second OPINION, which is
    what the architecture argues against, not a second source."""
    assert should_rerun(_claim(), _rec(verdict), str(repo)) is False


@pytest.mark.parametrize("ct", [ClaimType.EDIT, ClaimType.COMMIT, ClaimType.READ,
                                ClaimType.DESIGN_PROPERTY])
def test_only_claims_a_rerun_can_answer(ct: ClaimType, repo: pathlib.Path) -> None:
    """Tier 3 answers 'does it pass'. It cannot prove a file was edited or a page was read.

    `OTHER` is deliberately absent: it does not mean "not runnable", it means "unclassified", and
    the gate falls back to the text classifier for it -- see
    test_the_gate_works_on_claims_the_shipping_path_produces, which is the case that matters since
    review.py labels everything OTHER.
    """
    assert should_rerun(_claim(ct), _rec(), str(repo)) is False


def test_an_unclassifiable_claim_is_not_re_run(repo: pathlib.Path) -> None:
    """OTHER plus text that is not about running anything: still no."""
    c = Claim(id="c1", session_id="s", text="The architecture is cleaner this way.",
              type=ClaimType.OTHER)
    assert should_rerun(c, _rec(), str(repo)) is False


def test_no_repo_means_no_rerun(tmp_path: pathlib.Path) -> None:
    """`rerun_tests` materialises a worktree; with no git tree it raises. PR #51's review found
    exactly this swallowed by an `except Exception` in the eval harness, where it silently moved
    zero claims."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert should_rerun(_claim(), _rec(), str(plain)) is False
    assert should_rerun(_claim(), _rec(), None) is False
    assert should_rerun(_claim(), _rec(), "/nonexistent/path") is False


def test_no_committed_runner_config_means_nothing_to_run(tmp_path: pathlib.Path) -> None:
    """Auto-detection reads HEAD. A repo with no test config would fail for reasons that have
    nothing to do with the claim."""
    bare = tmp_path / "bare"
    bare.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=bare, check=True)
    (bare / "readme.md").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=bare, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
                   cwd=bare, check=True)
    assert should_rerun(_claim(), _rec(), str(bare)) is False


def test_the_same_tree_is_never_re_run_twice(repo: pathlib.Path) -> None:
    """Same commit plus same working tree gives the same answer. Repeating it is pure latency."""
    key = rerun_key(_claim(), str(repo))
    assert key is not None
    assert should_rerun(_claim(), _rec(), str(repo), already={key}) is False


def test_a_changed_tree_is_re_run_again(repo: pathlib.Path) -> None:
    """The agent fixed something, so the answer can differ -- that is new evidence, not a retry."""
    first = rerun_key(_claim(), str(repo))
    (repo / "newfile.py").write_text("x = 1\n", encoding="utf-8")
    assert should_rerun(_claim(), _rec(), str(repo), already={first}) is True, \
        "a real change to the tree must be re-runnable"


def test_the_budget_stops_a_spin(repo: pathlib.Path) -> None:
    """The bound that makes the worst case finite regardless of how many claims are open."""
    spent = {f"c{i}:x" for i in range(RERUN_BUDGET_PER_SESSION)}
    assert should_rerun(_claim(), _rec(), str(repo), already=spent) is False


def test_the_gate_launches_nothing_by_itself(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """It is a predicate. If deciding could execute, a bug in the decision would be a bug that
    runs commands."""
    import receipts.rerun as rerun_mod

    def boom(*a: object, **k: object) -> object:
        raise AssertionError("the gate executed something while deciding")

    monkeypatch.setattr(rerun_mod, "rerun_tests", boom)
    monkeypatch.setattr(rerun_mod, "spawn_async", boom)
    should_rerun(_claim(), _rec(), str(repo))


# --- the integration bug: the gate could never have fired in production --------------------------

def test_the_gate_works_on_claims_the_shipping_path_produces(repo: pathlib.Path) -> None:
    """`review.py` labels EVERY claim `ClaimType.OTHER`.

    It extracts and judges in one call but does not classify, so a gate keyed on the type alone
    could only ever fire on the superseded ladder -- that is, never. Found by running it end to
    end rather than by reading it. The fallback is `claims.classify`, the same deterministic
    text classifier the ladder uses.
    """
    c = Claim(id="c1", session_id="s", text="I ran the test suite and everything passes.",
              type=ClaimType.OTHER)
    assert should_rerun(c, _rec(), str(repo)) is True

    unrelated = Claim(id="c2", session_id="s", text="I updated the README wording.",
                      type=ClaimType.OTHER)
    assert should_rerun(unrelated, _rec(), str(repo)) is False
