"""The policy file. docs/SCOPE.md §6.4, issue #64.

Everything here is additive: a policy widens what's scratch or protected, or adds a RED command
substring, and an empty or missing file must classify identically to no policy at all.
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

from custos_code.rules import RepoState
from custos_code.scope import Band, Grant, Policy, classify


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i",
                   "--allow-empty"], cwd=tmp_path, check=True)
    return tmp_path


def test_missing_policy_file_is_the_empty_policy(tmp_path: pathlib.Path) -> None:
    assert Policy.load(str(tmp_path / "nope.toml")) == Policy()


def test_a_malformed_policy_file_fails_open_to_the_empty_policy(tmp_path: pathlib.Path) -> None:
    bad = tmp_path / "policy.toml"
    bad.write_text("this is not [ valid toml", encoding="utf-8")
    assert Policy.load(str(bad)) == Policy()


def test_policy_widens_scratch(tmp_path: pathlib.Path) -> None:
    extra = tmp_path / "scratch-extra"
    extra.mkdir()
    p = tmp_path / "policy.toml"
    p.write_text(f'[scope]\nscratch = ["{extra}"]\n', encoding="utf-8")
    policy = Policy.load(str(p))
    proj = tmp_path / "proj"
    proj.mkdir()
    grant = Grant.for_session(str(proj), policy=policy)
    assert str(extra.resolve()) in grant.scratch


def test_policy_red_blocks_a_phrase_not_in_the_builtin_list(repo: pathlib.Path) -> None:
    p = repo / "policy.toml"
    p.write_text('[scope]\nred = ["terraform destroy"]\n', encoding="utf-8")
    policy = Policy.load(str(p))
    grant = Grant.for_session(str(repo))
    f = classify("Bash", {"command": "terraform destroy -auto-approve"}, grant,
                RepoState(str(repo)), policy)
    assert f.band is Band.RED and f.rule == "policy-red"


def test_policy_red_is_additive_not_a_replacement(repo: pathlib.Path) -> None:
    """A policy with unrelated red phrases must not disable a built-in RED rule."""
    p = repo / "policy.toml"
    p.write_text('[scope]\nred = ["terraform destroy"]\n', encoding="utf-8")
    policy = Policy.load(str(p))
    grant = Grant.for_session(str(repo))
    f = classify("Bash", {"command": "git push --force origin main"}, grant,
                RepoState(str(repo)), policy)
    assert f.band is Band.RED and f.rule == "git-force-push"


def test_policy_protects_an_extra_glob(repo: pathlib.Path) -> None:
    p = repo / "policy.toml"
    p.write_text('[scope]\nprotect = ["**/.secrets.yml"]\n', encoding="utf-8")
    policy = Policy.load(str(p))
    grant = Grant.for_session(str(repo))
    f = classify("Write", {"file_path": str(repo / "config" / ".secrets.yml")}, grant,
                RepoState(str(repo)), policy)
    assert f.band is Band.RED and f.rule == "protected-path"


def test_no_policy_classifies_identically_to_an_empty_one(repo: pathlib.Path) -> None:
    grant = Grant.for_session(str(repo))
    a = classify("Bash", {"command": "pip install requests"}, grant, RepoState(str(repo)), None)
    b = classify("Bash", {"command": "pip install requests"}, grant, RepoState(str(repo)), Policy())
    assert a == b


def test_max_files_changed_is_parsed_but_not_yet_enforced(tmp_path: pathlib.Path) -> None:
    """SCOPE.md §7: thresholds stay unset until the real corpus calibrates them."""
    p = tmp_path / "policy.toml"
    p.write_text("[scope]\nmax_files_changed = 5\n", encoding="utf-8")
    policy = Policy.load(str(p))
    assert policy.max_files_changed == 5
