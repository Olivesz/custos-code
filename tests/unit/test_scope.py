"""Scope banding. docs/SCOPE.md §4.

The load-bearing test in here is `test_the_cart_service_session_is_entirely_green`. That session
(2026-09-19) is the design constraint: an agent asked to fix failing tests also built a scratchpad
venv, a repro directory and a mutation test. All of it was good work and none of it was requested.
A scope rule that fires on any of it is too tight and is wrong, however principled it looks.

Everything else guards the other direction: the actions that must stop regardless of how helpful
the agent was being.
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

from custos_code.rules import RepoState
from custos_code.scope import Band, Grant, classify, recoverable


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "cart.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
                   cwd=tmp_path, check=True)
    return tmp_path


def _g(repo: pathlib.Path, **kw: object) -> Grant:
    return Grant.for_session(str(repo), **kw)  # type: ignore[arg-type]


# --- the constraint -----------------------------------------------------------------------------

CART_SERVICE_SESSION = [
    ("Bash", {"command": "ls -la"}),
    ("Bash", {"command": "cat README.md"}),
    ("Read", {"file_path": "/etc/hosts"}),
    ("Bash", {"command": "pytest -q"}),
    ("Bash", {"command": "git status --short"}),
    ("Bash", {"command": "git mv tests/checks.py tests/test_cart.py"}),
    ("Write", {"file_path": "conftest.py"}),
    ("Bash", {"command": "python3 -m venv /tmp/scratch/venv"}),
    ("Bash", {"command": "rm -rf /tmp/scratch/repro"}),
    ("Bash", {"command": "git diff HEAD -- src/cart.py"}),
    ("Edit", {"file_path": "src/cart.py"}),
]


@pytest.mark.parametrize("tool,inp", CART_SERVICE_SESSION,
                         ids=[f"{t}:{str(i)[:34]}" for t, i in CART_SERVICE_SESSION])
def test_the_cart_service_session_is_entirely_green(tool: str, inp: dict, repo: pathlib.Path) -> None:
    """Real accepted work must never gate. Every hit here is a false positive by construction."""
    f = classify(tool, inp, _g(repo), RepoState(str(repo)))
    assert f.band is Band.GREEN, f"would have interrupted good work: {f.rule} -- {f.detail}"


# --- must stop ----------------------------------------------------------------------------------

RED_CASES = [
    ("rm -rf outside scratch", "Bash", {"command": "rm -rf /Users/someone/Projects"}, "rm-recursive-force"),
    ("force push",             "Bash", {"command": "git push --force origin main"},   "git-force-push"),
    ("force push -f",          "Bash", {"command": "git push -f origin main"},        "git-force-push"),
    ("reset --hard",           "Bash", {"command": "git reset --hard HEAD~3"},        "git-reset-hard"),
    ("sudo",                   "Bash", {"command": "sudo rm /etc/hosts"},             "sudo"),
    ("publish",                "Bash", {"command": "npm publish"},                    "package-publish"),
    ("ssh key",                "Write", {"file_path": "~/.ssh/config"},               "protected-path"),
    ("aws creds",              "Write", {"file_path": "~/.aws/credentials"},          "protected-path"),
    ("dotenv",                 "Write", {"file_path": "~/other/.env"},                "protected-path"),
]


@pytest.mark.parametrize("name,tool,inp,rule", RED_CASES, ids=[c[0] for c in RED_CASES])
def test_red_blocks(name: str, tool: str, inp: dict, rule: str, repo: pathlib.Path) -> None:
    f = classify(tool, inp, _g(repo), RepoState(str(repo)))
    assert f.band is Band.RED, f"{name} was not RED: {f.band} {f.rule}"
    assert f.rule == rule
    assert not f.recoverable


YELLOW_CASES = [
    ("write outside cwd", "Write", {"file_path": "/Users/someone/.zshrc"}, "write-outside-cwd"),
    ("dep install",       "Bash", {"command": "pip install requests"},     "dependency-install"),
    ("egress",            "Bash", {"command": "curl https://example.com"}, "network-egress"),
    ("plain push",        "Bash", {"command": "git push origin main"},     "git-push"),
]


@pytest.mark.parametrize("name,tool,inp,rule", YELLOW_CASES, ids=[c[0] for c in YELLOW_CASES])
def test_yellow_asks(name: str, tool: str, inp: dict, rule: str, repo: pathlib.Path) -> None:
    f = classify(tool, inp, _g(repo), RepoState(str(repo)))
    assert f.band is Band.YELLOW, f"{name}: {f.band} {f.rule}"
    assert f.rule == rule


# --- the properties the design rests on ---------------------------------------------------------

def test_rm_rf_confined_to_scratch_is_green(repo: pathlib.Path) -> None:
    """Agents clean up after themselves; that must not be a blocking event."""
    f = classify("Bash", {"command": "rm -rf /tmp/scratch/build"}, _g(repo), RepoState(str(repo)))
    assert f.band is Band.GREEN


def test_a_sibling_directory_is_not_inside_the_grant(tmp_path: pathlib.Path) -> None:
    """`/x/proj-evil` must not count as inside `/x/proj` -- startswith says it does.

    Explicit empty scratch: on macOS `tmp_path` sits under $TMPDIR, so the sibling would be
    legitimately GREEN as scratch and the prefix property would go untested. This asserts the
    path logic, not the accident of where pytest puts its fixtures.
    """
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj-evil").mkdir()
    g = Grant(cwd=str(tmp_path / "proj"), scratch=())
    f = classify("Write", {"file_path": str(tmp_path / "proj-evil" / "x.py")}, g,
                 RepoState(str(tmp_path / "proj")))
    assert f.band is Band.YELLOW and f.rule == "write-outside-cwd"


def test_approved_paths_are_not_asked_about_twice(tmp_path: pathlib.Path) -> None:
    """Ratchet: a gate that re-asks is a gate people disable (SCOPE.md §5)."""
    (tmp_path / "proj").mkdir()
    (tmp_path / "other").mkdir()
    target = str(tmp_path / "other" / "x.py")
    g0 = Grant(cwd=str(tmp_path / "proj"), scratch=())      # see the sibling test for why
    assert classify("Write", {"file_path": target}, g0, RepoState(str(tmp_path / "proj"))).gates
    g1 = Grant(cwd=str(tmp_path / "proj"), scratch=(), approved=(str(tmp_path / "other"),))
    assert not classify("Write", {"file_path": target}, g1, RepoState(str(tmp_path / "proj"))).gates


def test_writes_are_recoverable_in_a_git_tree_and_not_outside_one(
        repo: pathlib.Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    bare = tmp_path_factory.mktemp("nogit")
    g_repo, g_bare = _g(repo), Grant.for_session(str(bare))
    assert recoverable(str(repo / "src" / "cart.py"), g_repo, RepoState(str(repo)))
    assert not recoverable(str(bare / "x.py"), g_bare, RepoState(str(bare)))


def test_an_unrecoverable_in_radius_write_is_yellow(tmp_path: pathlib.Path) -> None:
    """Inside the grant but with no git to undo it: worth a question, not a block."""
    d = tmp_path / "plain"
    d.mkdir()
    f = classify("Write", {"file_path": str(d / "x.py")}, Grant.for_session(str(d)), RepoState(str(d)))
    assert f.band is Band.YELLOW and f.rule == "unrecoverable-write"
    assert not f.recoverable


def test_reads_are_free_everywhere(repo: pathlib.Path) -> None:
    for tool, inp in (("Read", {"file_path": "~/.ssh/config"}),
                      ("Grep", {"path": "/"}),
                      ("Bash", {"command": "cat ~/.aws/credentials"})):
        assert classify(tool, inp, _g(repo), RepoState(str(repo))).band is Band.GREEN, (tool, inp)


def test_a_redirect_makes_a_read_command_a_write(repo: pathlib.Path) -> None:
    """`ls > ~/.zshrc` is not a read, and the read-only allowlist must not launder it."""
    f = classify("Bash", {"command": "ls > /Users/someone/.zshrc"}, _g(repo), RepoState(str(repo)))
    assert f.band is not Band.GREEN


def test_classify_is_pure(repo: pathlib.Path) -> None:
    """No model call, no network: identical inputs give identical findings, every time."""
    args = ("Bash", {"command": "git push --force"}, _g(repo), RepoState(str(repo)))
    first = classify(*args)
    assert all(classify(*args) == first for _ in range(5))


# --- compound commands: a chained line is not one opaque, unrecognised blob ---------------------
#
# Found by dogfooding the installed hooks on this very session: `cd repo && source ~/.env && uv
# run ...` was reported as "writes ~/.env" (RED, protected-path), because the old check only
# looked at the line's FIRST token ("cd", not in the allowlist) and then treated "not recognised"
# as "might write anything the line mentions" -- including a path a `source` two steps later only
# ever reads. Nothing here should write anything; it should all be GREEN or exactly as YELLOW/RED
# as the one segment that actually does something.

def test_sourcing_a_file_in_a_chain_does_not_read_as_writing_it(repo: pathlib.Path) -> None:
    outside_env = "/Users/someone/.env"
    f = classify("Bash", {"command": f'cd "{repo}" && source "{outside_env}" && echo done'},
                _g(repo), RepoState(str(repo)))
    assert f.band is Band.GREEN, f"a read inside a chain was reported as a write: {f.rule} {f.detail}"


def test_sourcing_a_file_alone_is_read_only(repo: pathlib.Path) -> None:
    f = classify("Bash", {"command": "source /Users/someone/.env"}, _g(repo), RepoState(str(repo)))
    assert f.band is Band.GREEN


def test_a_fully_recognised_chain_is_green_even_though_cd_leads_it(repo: pathlib.Path) -> None:
    """`cd` never used to be in the allowlist, so ANY chain starting with it fell through as
    unrecognised, not as safe -- this is a straightforward improvement, not just a bug fix."""
    f = classify("Bash", {"command": f'cd "{repo}" && pytest -q'}, _g(repo), RepoState(str(repo)))
    assert f.band is Band.GREEN


def test_an_unrecognised_step_in_the_chain_still_leaves_the_whole_thing_ungreen(
        repo: pathlib.Path) -> None:
    """The fix narrows false positives; it must not launder a step we genuinely don't recognise.

    A path an unrecognised segment touches INSIDE the granted, git-backed cwd is meant to be
    GREEN regardless of which tool wrote it -- that is the GREEN band's own definition. This
    instead points the unrecognised step outside the grant, where it must still gate.
    """
    f = classify("Bash", {"command": f'cd "{repo}" && ./some-custom-script.sh /Users/someone/out.txt'},
                _g(repo), RepoState(str(repo)))
    assert f.band is Band.YELLOW and f.rule == "write-outside-cwd"


def test_a_real_write_to_a_protected_path_is_still_caught_inside_a_chain(
        repo: pathlib.Path) -> None:
    """Narrowing to the writing segment must not stop catching an actual write in one."""
    f = classify("Bash", {"command": f'cd "{repo}" && cp secret.txt ~/.ssh/config'},
                _g(repo), RepoState(str(repo)))
    assert f.band is Band.RED and f.rule == "protected-path"


def test_a_redirect_two_steps_into_a_chain_is_still_a_write(repo: pathlib.Path) -> None:
    f = classify("Bash", {"command": f'cd "{repo}" && echo hi > /Users/someone/.zshrc'},
                _g(repo), RepoState(str(repo)))
    assert f.band is not Band.GREEN


# --- holes found while writing the tests above --------------------------------------------------

def test_a_scratch_root_itself_is_not_disposable() -> None:
    """`rm -rf /tmp/scratch/build` is tidying up. `rm -rf /tmp` is not.

    A plain "is the target under a scratch root" test waves the second one through, because a
    directory is trivially under itself. Found by these tests, not in review.
    """
    g = Grant.for_session("/Users/someone/proj")
    assert classify("Bash", {"command": "rm -rf /tmp"}, g).band is Band.RED
    assert classify("Bash", {"command": "rm -rf /tmp/scratch/build"}, g).band is Band.GREEN


def test_a_project_living_under_tmpdir_is_not_scratch(tmp_path: pathlib.Path) -> None:
    """CI runners, git worktrees and containers all sit under $TMPDIR or /tmp.

    Treating the granted directory as disposable because of where it happens to live would make
    the checker silently inert in exactly those environments. Asserts the BEHAVIOUR -- a write
    inside the project is banded normally -- not the contents of `grant.scratch`, because the
    first fix for this dropped whole scratch roots and broke cleanup everywhere else (#60 review).
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    g = Grant.for_session(str(proj))
    f = classify("Write", {"file_path": str(proj / "x.py")}, g, RepoState(str(proj)))
    assert f.band is Band.YELLOW and f.rule == "unrecoverable-write", \
        "a project under a scratch root was waved through as disposable"


def test_scratch_siblings_survive_a_project_that_lives_under_scratch(tmp_path: pathlib.Path) -> None:
    """The regression Anush caught: keeping cwd out of scratch must not delete the whole root.

    A project at /tmp/proj must not stop /tmp/scratch/build from being disposable -- that is what
    turned a routine `rm -rf` of a build dir into RED on Linux CI, and would have done the same in
    any container running under /tmp.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    g = Grant.for_session(str(proj))
    sibling = str(tmp_path / "build-artifacts")
    assert classify("Bash", {"command": f"rm -rf {sibling}"}, g,
                    RepoState(str(proj))).band is Band.GREEN
    assert classify("Write", {"file_path": str(proj / "x.py")}, g,
                    RepoState(str(proj))).band is Band.YELLOW
