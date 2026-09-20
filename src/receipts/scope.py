"""The second checker: did the agent do what it was ASKED, not just what it said?

`rules.py` and `review.py` answer integrity -- did the claim happen. This answers scope -- was the
action inside the boundary the user granted. Design and rationale: docs/SCOPE.md.

**Scope is a blast radius, not a task description.** The design constraint is a real session
(cart-service, 2026-09-19): asked to fix failing tests, the agent also built a scratchpad venv, a
repro directory, and ran a mutation test. Good work, none of it requested. A rule keyed on "you
touched a file outside tests/" would have blocked it four times and been wrong every time.

What made that fine was not proximity to the request -- the mutation test was nowhere near it. It
was that every step was RECOVERABLE: temp dirs and uncommitted working-tree changes, all undoable
with `git checkout` or `rm -rf /tmp/...`.

    Recoverability is the wiggle room. If git or a scratch directory can undo it, the agent may be
    as creative as it likes and we do not spend a token looking.

Three bands (docs/SCOPE.md §4):
  GREEN   never gate, zero cost. Reads; writes git can revert; scratch dirs; tests/builds/linters.
  YELLOW  pause and ask. Writes outside cwd, unrecoverable edits, dependency installs, egress.
  RED     block always, attended or not. Irreversible AND outside the radius.

Everything here is PURE and DETERMINISTIC: no model call, no network, no subprocess beyond the git
queries `RepoState` already caches. That is what keeps GREEN free and keeps alpha at zero for the
bands that block -- and per docs/SCOPE.md §5, only grounded scope may block. The moment a block
rests on a model's judgement, alpha climbs and the stopping arithmetic in §2 turns against us.

Thresholds here are DEFAULTS, not findings. Issue #57 calibrates them against ~400 local sessions
of accepted work, where every YELLOW or RED is a false positive by construction. Until that lands,
treat the magnitude rules as unset.

Owner: Oliver.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .rules import RepoState


class Band(StrEnum):
    GREEN = "green"    # no gate, no cost
    YELLOW = "yellow"  # pause and ask (attended) / block (unattended)
    RED = "red"        # block always


# Irreversible by nature. These are matched against the command text, so they fire whether or not
# we can resolve the target path -- an `rm -rf` we cannot localise is exactly the case to stop.
_RED_COMMAND = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*[rR])\b"), "rm-recursive-force"),
    (re.compile(r"\bgit\s+push\b[^|;&]*(--force\b|(?<!-)-f\b)"), "git-force-push"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "git-reset-hard"),
    (re.compile(r"\bgit\s+clean\b[^|;&]*-[a-zA-Z]*f"), "git-clean-force"),
    (re.compile(r"(^|[|;&]\s*)sudo\b"), "sudo"),
    (re.compile(r"\b(npm|yarn|pnpm)\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b"), "package-publish"),
    (re.compile(r"\bgh\s+(release|repo)\s+(create|delete)\b|\bgit\s+push\b[^|;&]*--delete\b"), "remote-mutation"),
    (re.compile(r"\bshutdown\b|\breboot\b|\bdiskutil\b|\bmkfs\b"), "system-level"),
]

# Paths that are never in the blast radius, wherever they sit. A write here is RED even under $HOME.
_PROTECTED = (
    "~/.ssh", "~/.aws", "~/.gnupg", "~/.kube", "~/.docker/config.json",
    "~/.netrc", "~/.npmrc", "~/.pypirc", "~/.git-credentials",
)
_PROTECTED_SUFFIX = (".env", ".pem", ".key", "id_rsa", "id_ed25519", "credentials")

# Reaches the network or mutates the environment. Recoverable, but the user should know.
_YELLOW_COMMAND = [
    (re.compile(r"\b(pip|pip3|uv|npm|yarn|pnpm|cargo|go|gem|brew|apt|apt-get)\s+(install|add|get)\b"),
     "dependency-install"),
    (re.compile(r"\b(curl|wget|nc|ncat|ssh|scp|rsync|sftp)\b"), "network-egress"),
    (re.compile(r"\bgit\s+push\b"), "git-push"),
    (re.compile(r"\bcrontab\b|\blaunchctl\b|\bsystemctl\b"), "scheduling"),
]

# Reads and inspections. Free, always, regardless of where they point.
_READ_TOOLS = frozenset({"Read", "Glob", "Grep", "NotebookRead", "WebFetch", "WebSearch", "TodoWrite"})
_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "str_replace_based_edit_tool"})


@dataclass(frozen=True)
class Grant:
    """The blast radius the user allowed, explicitly and implicitly.

    `cwd` is the implicit grant: invoking an agent in a directory grants that directory. `named`
    is what the request mentioned. `approved` is ratcheted -- once the user approves a path this
    session we never ask again, because a gate that re-asks is a gate people turn off (docs/SCOPE.md
    §5), and a disabled gate verifies nothing.
    """
    cwd: str
    scratch: tuple[str, ...] = ()
    named: tuple[str, ...] = ()
    approved: tuple[str, ...] = ()

    @staticmethod
    def for_session(cwd: str, named: tuple[str, ...] = (), approved: tuple[str, ...] = ()) -> Grant:
        scratch = [os.path.realpath(p) for p in
                   (os.environ.get("TMPDIR", "/tmp"), "/tmp", "/private/tmp",
                    os.path.expanduser("~/.receipts")) if p]
        root = os.path.realpath(os.path.expanduser(cwd)) if cwd else ""
        # Scratch roots are kept whole. An earlier version dropped any root that CONTAINED the
        # project -- which deleted /tmp from the list whenever cwd was anywhere beneath it, so
        # /tmp/scratch/build stopped being scratch and a routine cleanup became RED. It broke CI
        # (pytest's tmp_path lives under /tmp on Linux) and, worse, would have silently removed
        # scratch protection from any real session running in a container or sandbox under /tmp.
        # Caught by Anush in review on #60.
        #
        # The grant still wins, but only where it actually applies: `_in_scratch` excludes paths
        # inside cwd, so a project living in /tmp is banded normally while its siblings under /tmp
        # remain disposable.
        return Grant(cwd=root, scratch=tuple(dict.fromkeys(scratch)), named=named, approved=approved)


@dataclass(frozen=True)
class Finding:
    band: Band
    rule: str          # stable id, so calibration (#57) can bucket by cause
    detail: str        # one line naming the path or command
    recoverable: bool

    @property
    def gates(self) -> bool:
        return self.band is not Band.GREEN


GREEN_OK = Finding(Band.GREEN, "in-radius", "", recoverable=True)


def _under(path: str, root: str) -> bool:
    """True when `path` is inside `root`.

    `os.path.commonpath`, never `startswith`: `/x/proj-evil` is not inside `/x/proj`, and a
    prefix test says it is. Same bug class as the RECEIPTS_ONLY_IN fence in hooks.py.
    """
    if not root:
        return False
    try:
        return os.path.commonpath([os.path.realpath(root), os.path.realpath(path)]) == os.path.realpath(root)
    except (ValueError, OSError):
        return False


def _abs(path: str, cwd: str) -> str:
    p = os.path.expanduser(path)
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(cwd or os.getcwd(), p))


def _protected(abs_path: str) -> str | None:
    for p in _PROTECTED:
        if _under(abs_path, os.path.expanduser(p)) or abs_path == os.path.expanduser(p):
            return p
    base = os.path.basename(abs_path)
    for suf in _PROTECTED_SUFFIX:
        if base == suf or base.endswith(suf):
            return suf
    return None


def _in_scratch(abs_path: str, grant: Grant) -> bool:
    """Inside a scratch directory -- but never a scratch root itself.

    `rm -rf /tmp/scratch/build` is an agent tidying up. `rm -rf /tmp` is not, and a plain
    "is it under a scratch root" test waves it through because a directory is trivially under
    itself. The root belongs to the machine, not to the session.
    """
    rp = os.path.realpath(abs_path)
    if grant.cwd and _under(rp, grant.cwd):
        return False          # inside the granted project: band it normally, wherever it lives
    return any(_under(rp, s) and rp != os.path.realpath(s) for s in grant.scratch)


def recoverable(abs_path: str, grant: Grant, state: RepoState) -> bool:
    """Can this write be undone without the user losing anything?

    Scratch is free by definition. Inside cwd, git is the undo mechanism: in a work tree, both a
    tracked edit and a new untracked file are revertible (`git checkout` / delete). Outside both,
    assume not -- that is the conservative direction, and it is only ever used to escalate a
    YELLOW, never to justify a RED on its own.
    """
    if _in_scratch(abs_path, grant):
        return True
    if _under(abs_path, grant.cwd) and state.is_git:
        return True
    return False


def _paths_in(tool: str, inp: dict[str, Any]) -> list[str]:
    out = [v for k in ("file_path", "path", "notebook_path")
           if isinstance(v := inp.get(k), str) and v]
    if tool == "Bash" and isinstance(cmd := inp.get("command"), str):
        try:
            toks = shlex.split(cmd)
        except ValueError:
            toks = cmd.split()
        out += [t for t in toks if ("/" in t or t.startswith("~")) and not t.startswith("-")]
    return out


def classify(tool: str, tool_input: dict[str, Any], grant: Grant,
             state: RepoState | None = None) -> Finding:
    """Band one tool call. Pure, deterministic, no model call.

    Order matters: RED first (an irreversible action is RED wherever it points), then reads (free),
    then writes and the YELLOW command families. The first match wins, so a `sudo rm -rf` reports
    as `rm-recursive-force` rather than as whichever rule happens to be checked last.
    """
    st = state if state is not None else RepoState(grant.cwd or None)
    inp = tool_input or {}
    cmd = inp.get("command") if isinstance(inp.get("command"), str) else ""

    # --- RED: irreversible, wherever it points -------------------------------------------------
    if tool == "Bash" and cmd:
        for pat, rule in _RED_COMMAND:
            if pat.search(cmd):
                # An rm -rf confined to scratch is how agents clean up after themselves.
                if rule == "rm-recursive-force":
                    targets = [_abs(p, grant.cwd) for p in _paths_in(tool, inp)]
                    if targets and all(_in_scratch(t, grant) for t in targets):
                        return GREEN_OK
                return Finding(Band.RED, rule, f"{rule}: {cmd[:160]}", recoverable=False)

    for raw in _paths_in(tool, inp):
        p = _abs(raw, grant.cwd)
        if (hit := _protected(p)) and (tool in _WRITE_TOOLS or (tool == "Bash" and cmd and
                                                                not _is_read_only_cmd(cmd))):
            return Finding(Band.RED, "protected-path", f"writes {hit}: {raw}", recoverable=False)

    # --- GREEN: reads are free, everywhere -----------------------------------------------------
    if tool in _READ_TOOLS:
        return GREEN_OK
    if tool == "Bash" and cmd and _is_read_only_cmd(cmd):
        return GREEN_OK

    # --- writes: banded by recoverability, not by distance from the request --------------------
    if tool in _WRITE_TOOLS or (tool == "Bash" and cmd):
        for raw in _paths_in(tool, inp):
            p = _abs(raw, grant.cwd)
            if _in_scratch(p, grant):
                continue
            if any(_under(p, _abs(a, grant.cwd)) for a in grant.approved):
                continue                                   # ratcheted: never ask twice
            if not _under(p, grant.cwd):
                return Finding(Band.YELLOW, "write-outside-cwd",
                               f"writes outside the granted directory: {raw}",
                               recoverable=recoverable(p, grant, st))
            if not recoverable(p, grant, st):
                return Finding(Band.YELLOW, "unrecoverable-write",
                               f"not revertible (no git work tree here): {raw}",
                               recoverable=False)

    # --- YELLOW command families ---------------------------------------------------------------
    if tool == "Bash" and cmd:
        for pat, rule in _YELLOW_COMMAND:
            if pat.search(cmd):
                return Finding(Band.YELLOW, rule, f"{rule}: {cmd[:160]}", recoverable=True)

    return GREEN_OK


_READ_ONLY_FIRST = frozenset({
    "ls", "cat", "head", "tail", "grep", "rg", "find", "wc", "diff", "stat", "file", "which",
    "pwd", "echo", "printf", "date", "env", "tree", "du", "df", "ps", "sed", "awk", "sort", "uniq",
    "pytest", "npx", "node", "python", "python3", "go", "cargo", "make", "ruff", "mypy", "tsc",
    "eslint", "jest", "vitest",
})
_GIT_READ_ONLY = frozenset({"status", "log", "diff", "show", "branch", "remote", "rev-parse",
                            "ls-files", "blame", "describe", "config", "stash"})


def _is_read_only_cmd(cmd: str) -> bool:
    """Conservative: only commands we recognise as non-mutating, and only without a redirect.

    A redirect turns any of these into a write (`ls > file`), so the presence of `>` disqualifies
    the whole line. Being wrong here costs a needless YELLOW, not a missed RED.
    """
    if ">" in cmd:
        return False
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False
    if not parts:
        return False
    head = os.path.basename(parts[0])
    if head == "git":
        return len(parts) > 1 and parts[1] in _GIT_READ_ONLY
    return head in _READ_ONLY_FIRST
