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

import functools
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


# Irreversible by nature. Matched per SEGMENT (see `_segments`), so a dangerous command cannot be
# laundered by putting an allowlisted one in front of it: `echo hi && rm -rf ~` is two segments.
#
# `_GIT` allows the option soup git accepts between the binary and the subcommand -- `git -c k=v`,
# `git --no-pager`. Without it, `git -c protocol.version=2 push --force` sailed past the flagship
# RED rule entirely (adversarial review of #60, 2026-09-20).
_GIT = r"\bgit\b(?:\s+(?:-c\s+\S+|--no-pager|--git-dir=\S+|--work-tree=\S+|-C\s+\S+))*\s+"
_RM_FLAGS = r"(?:\s+-[a-zA-Z]+|\s+--recursive|\s+--force)*"
_RED_COMMAND = [
    # -rf, -r -f, -R --force, --recursive --force: any spelling that is both recursive and forced.
    (re.compile(r"\brm\b(?=" + _RM_FLAGS + r"\s)(?=(?:.*(?:-[a-zA-Z]*[rR]|--recursive)))"
                r"(?=(?:.*(?:-[a-zA-Z]*f|--force)))"), "rm-recursive-force"),
    (re.compile(_GIT + r"push\b.*?(--force\b|--force-with-lease\b|(?<!-)-f\b|\s\+[\w./-]+)"), "git-force-push"),
    (re.compile(_GIT + r"reset\s+--hard\b"), "git-reset-hard"),
    (re.compile(_GIT + r"clean\b.*-[a-zA-Z]*f"), "git-clean-force"),
    (re.compile(r"^\s*sudo\b|\bdoas\b"), "sudo"),
    (re.compile(r"\b(npm|yarn|pnpm)\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b"), "package-publish"),
    (re.compile(r"\bgh\s+(release|repo)\s+(create|delete)\b|" + _GIT + r"push\b.*--delete\b"), "remote-mutation"),
    # `shutdown` and `reboot` were here and produced ZERO true positives across 404 sessions --
    # every fire was the word appearing inside Python source or prose. Now that interpreter
    # heredoc bodies are scanned as commands (they must be, or `bash <<EOF` bypasses everything),
    # keeping those two keywords would reinstate exactly that false positive. `diskutil`, `mkfs`
    # and `dd of=` carry this rule; they do not appear in ordinary prose.
    (re.compile(r"\bdiskutil\b|\bmkfs\b|\bdd\b[^|;&]*\bof="), "system-level"),
    (re.compile(r"\bfind\b.*(-delete\b|-exec\b)"), "find-mutating"),
    (re.compile(r"\b(mkfs|fdisk|parted)\b"), "system-level"),
]

# Matched against the WHOLE line, never a segment: these rules describe the join BETWEEN segments,
# so splitting on `|` destroys exactly the thing they look for. Executing content that is not in
# the command line -- `curl ... | sh`, `base64 -d | sh`, `eval "$X"` -- means nothing downstream
# can band what actually runs, so the pipe itself is the decision point.
_RED_WHOLE_LINE = [
    # Only when the piped-in content came off the NETWORK. `cat f | python3` and
    # `... | python3 -c` are everyday idioms -- 136 fires on accepted work, all benign.
    (re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(sh|bash|zsh|ksh|python3?|node|perl|ruby)\b"),
     "pipe-network-to-interpreter"),
    (re.compile(r"(^|[;&|]\s*)eval\b"), "eval"),
    (re.compile(r"\bbase64\b[^|]*-{1,2}d(ecode)?\b"), "base64-decode"),
]

# Paths that are never in the blast radius, wherever they sit.
#
# ~/.receipts is on this list because the adversarial review showed the agent could write its own
# permission state, its own config, and -- worst -- the append-only hash-chained LEDGER, all as
# GREEN, because scope.py had declared the whole directory disposable scratch. An agent that can
# write {"scope_approved": ["/"]} grants itself everything; one that can write config.toml turns
# the gate off; one that can write live/*.jsonl has a model write path to the evidence, which is
# invariant 1 and the thing this entire project exists to prevent.
_PROTECTED = (
    "~/.receipts",
    "~/.ssh", "~/.aws", "~/.gnupg", "~/.kube", "~/.docker/config.json",
    "~/.netrc", "~/.npmrc", "~/.pypirc", "~/.git-credentials", "~/.gitconfig",
)
# Exact basenames only. An `endswith` test made every test fixture named `server.key` or
# `dummy.pem` un-writable AND un-approvable, which is the "gate gets switched off" failure.
_PROTECTED_NAMES = frozenset({
    "id_rsa", "id_ed25519", "id_ecdsa", "credentials", ".git-credentials", ".npmrc",
})
# Secret-bearing but plausibly the agent's job to edit inside its own project. RED would make any
# repo with a .env unworkable, which is how a gate gets switched off; YELLOW asks once and ratchets.
_ASK_NAMES = frozenset({".env", ".envrc", ".netrc"})

# Reaches the network or mutates the environment. Recoverable, but the user should know.
_YELLOW_COMMAND = [
    (re.compile(r"\b(pip|pip3|uv|npm|yarn|pnpm|cargo|go|gem|brew|apt|apt-get)\s+(install|add|get)\b"),
     "dependency-install"),
    (re.compile(r"\b(curl|wget|nc|ncat|ssh|scp|sftp)\b|\brsync\b.*::|\brsync\b.*@"), "network-egress"),
    (re.compile(_GIT + r"push\b"), "git-push"),
    (re.compile(r"\bcrontab\b|\blaunchctl\b|\bsystemctl\b"), "scheduling"),
    (re.compile(_GIT + r"config\b.*--global|" + _GIT + r"stash\b"), "git-state-mutation"),
    (re.compile(r"\b(sed|perl)\b.*\s-i\b"), "in-place-edit"),
]

# Tool names that act on the world outside the machine. SCOPE.md §4 lists sending messages and
# spending money as RED; an unknown MCP tool called `send_message` should not default to free.
# Narrowed hard after calibration: the first version matched `send`, which caught 586 calls to a
# LOCAL session-to-session message tool and 167 `SendUserFile`s -- neither leaves the machine.
# Only name a tool here if the action is externally visible and not undoable.
_TOOL_NAME_RED = re.compile(
    r"(?i)(send_(mail|email|sms)|send_message.*(slack|teams|gmail|outlook)|outlook_send|"
    r"gmail_send|slack_send|_publish|create_charge|payment|purchase|checkout|"
    r"deploy|delete_(repo|branch|release)|destroy|terminate)")

# Reads and inspections. Free, always, regardless of where they point.
_READ_TOOLS = frozenset({"Read", "Glob", "Grep", "NotebookRead", "TodoWrite"})
_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "str_replace_based_edit_tool"})
_EGRESS_TOOLS = frozenset({"WebFetch", "WebSearch"})


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


# The harness's own working data. The agent writing `~/.claude/projects/<p>/memory/*.md` or a
# skill is the documented mechanism, not a scope violation -- it was 1,234 YELLOW fires on
# accepted work. `settings.json` and `hooks/` are excluded, because those configure the very
# hooks doing the checking: an agent that can rewrite them can switch the gate off.
_HARNESS_OWNED = os.path.expanduser("~/.claude")
_HARNESS_PROTECTED = ("settings.json", "settings.local.json", "hooks")


def _harness_sanctioned(abs_path: str) -> bool:
    if not _under(abs_path, _HARNESS_OWNED):
        return False
    rel = os.path.relpath(abs_path, _HARNESS_OWNED).split(os.sep)
    return not any(part in _HARNESS_PROTECTED for part in rel)


def _protected_always(abs_path: str) -> bool:
    """Protected even when it sits inside the granted project.

    ~/.receipts holds the ledger, the loop state and the config. A project that happens to contain
    it must not thereby be allowed to rewrite its own evidence.
    """
    return _under(abs_path, os.path.expanduser("~/.receipts"))


def _protected(abs_path: str) -> str | None:
    for p in _PROTECTED:
        if _under(abs_path, os.path.expanduser(p)) or abs_path == os.path.expanduser(p):
            return p
    return os.path.basename(abs_path) if os.path.basename(abs_path) in _PROTECTED_NAMES else None


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
    return _nearest_git_root(abs_path) is not None


@functools.lru_cache(maxsize=2048)
def _nearest_git_root(abs_path: str) -> str | None:
    """The git work tree containing `abs_path`, found by walking up. None if there is none.

    `recoverable()` used to ask whether the GRANT ROOT was a git tree. On the accepted corpus,
    9,016 of 9,516 `unrecoverable-write` fires came from sessions rooted at `~/Projects` -- a
    directory that holds twenty repositories and is not itself one -- so every write into every
    one of those repos was reported as unrevertible. The question is about the target, not the
    root the agent happens to have been started in.
    """
    cur = os.path.dirname(abs_path) if not os.path.isdir(abs_path) else abs_path
    for _ in range(64):
        if os.path.isdir(os.path.join(cur, ".git")) or os.path.isfile(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent
    return None


# Commands whose arguments really are write targets. Everything else that mentions a path is
# reading it, cd-ing to it, or passing it to a program that may do anything.
_WRITE_VERBS = frozenset({"rm", "rmdir", "mv", "cp", "touch", "mkdir", "tee", "install",
                          "truncate", "chmod", "chown", "ln", "unlink", "dd", "shred"})


def _mentioned_paths(tool: str, inp: dict[str, Any]) -> list[str]:
    """Every path the call refers to, for RED rules that care about targets wherever they appear."""
    out = [v for k in ("file_path", "path", "notebook_path")
           if isinstance(v := inp.get(k), str) and v]
    if tool == "Bash" and isinstance(cmd := inp.get("command"), str):
        for seg in _segments(_strip_heredocs(cmd)):
            try:
                toks = shlex.split(seg)
            except ValueError:
                toks = seg.split()
            out += [x for x in toks if ("/" in x or x.startswith("~")) and not x.startswith("-")]
    return out


def _write_targets(tool: str, inp: dict[str, Any]) -> list[str]:
    """Only the paths this call actually WRITES.

    Calibration over 404 real sessions (2026-09-20) found this was the single defect that made
    the checker unusable: every `/`-bearing token in a Bash line was treated as a write target, so
    `cd ~/other-repo && python3 tools/x.py` was banded as a write to `~/other-repo`. 6,758 of
    9,516 `unrecoverable-write` fires and 1,333 of 1,897 `write-outside-cwd` fires came from that
    -- together about 40% of ALL tool calls, against a 1% budget. It measured vocabulary, not
    blast radius, and no threshold tuning could have fixed it.

    File tools state their target. A Bash line only has one if its verb writes, or if it
    redirects.
    """
    out = [v for k in ("file_path", "path", "notebook_path")
           if isinstance(v := inp.get(k), str) and v]
    if tool != "Bash":
        return out
    cmd = inp.get("command")
    if not isinstance(cmd, str):
        return out
    for seg in _segments(_strip_heredocs(cmd)):
        if (m := re.search(r"(?<![0-9])>>?\s*([^\s|;&]+)", seg)) is not None:
            out.append(m.group(1))            # a redirect target is a write, whatever the verb
        try:
            toks = shlex.split(seg)
        except ValueError:
            toks = seg.split()
        if not toks:
            continue
        if os.path.basename(toks[0]) in _WRITE_VERBS:
            out += [x for x in toks[1:] if not x.startswith("-")]
    return [x for x in out if _plausible_path(x)]


def _plausible_path(x: str) -> bool:
    """Reject shell fragments that survived splitting -- `/'`, bare globs, `$VAR`.

    Calibration produced findings whose detail read `touches a path outside the granted
    directory: /'`. Accusing someone over a quote fragment is how a gate loses credibility.
    """
    x = x.strip()
    return bool(x) and len(x) > 1 and not x.startswith("$") and x not in ("/", "//") \
        and not set(x) <= set("/'\"`*?")


# A heredoc fed to a shell or interpreter IS command text. One fed to `cat`, `jq` or a file is
# data. Blanket-stripping treated both as data, which made `bash <<'EOF' / rm -rf ~ / EOF` a
# complete bypass of every RED rule -- introduced by the fix for the 38 false `system-level` REDs
# and caught by the automated security review the same hour.
_INTERPRETER_HEAD = re.compile(
    r"\b(sh|bash|zsh|ksh|dash|python3?|perl|ruby|node|php|osascript|eval|sudo|env|ssh|tee)\b"
    r"|[|>]")   # a pipe or redirect after the delimiter is shell that keeps running
_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n(.*?)^\s*\1\s*$", re.S | re.M)


def _strip_heredocs(cmd: str) -> str:
    """Remove heredoc bodies that are DATA; keep the ones that are COMMANDS.

    Stripping exists because all 38 `system-level` REDs on the accepted corpus were the words
    `shutdown` or `reboot` appearing inside a Python heredoc body -- source code and prose, not a
    command line. But the same removal hid real commands when the heredoc feeds a shell.

    A body is kept when the text introducing it names an interpreter, on either side of the
    delimiter: `bash <<EOF` and `cat <<EOF | bash` both execute it. Kept bodies are re-emitted as
    their own lines so the segment scanner sees them as commands in their own right.
    """
    out: list[str] = []
    pos = 0
    for m in _HEREDOC.finditer(cmd):
        prefix = cmd[pos:m.start()]
        out.append(prefix)
        # The header tail is the shell that follows the delimiter on the same line -- the
        # `> ~/.zshrc` in `cat <<'EOF' > ~/.zshrc`. Dropping it with the body erased the redirect
        # and made writing anywhere GREEN. It is ALWAYS kept; only the body is ever removed.
        tail = m.group(2)
        header = prefix.rsplit("\n", 1)[-1] + tail
        body = "\n" + m.group(3) + "\n" if _INTERPRETER_HEAD.search(header) else "\n"
        out.append(tail + body)
        pos = m.end()
    out.append(cmd[pos:])
    return "".join(out)


def classify(tool: str, tool_input: dict[str, Any], grant: Grant,
             state: RepoState | None = None) -> Finding:
    """Band one tool call. Pure, deterministic, no model call.

    Order: RED first (irreversible wherever it points), then reads, then writes, then the YELLOW
    families. First match wins, so `sudo rm -rf` reports as the rm rule rather than whichever is
    checked last.
    """
    st = state if state is not None else RepoState(grant.cwd or None)
    inp = tool_input if isinstance(tool_input, dict) else {}
    raw_cmd = inp.get("command")
    cmd = raw_cmd if isinstance(raw_cmd, str) else ""
    # A Bash call whose command is not a string is malformed, not safe. It used to fall through
    # every branch to GREEN.
    if tool == "Bash" and not cmd:
        return Finding(Band.YELLOW, "unreadable-command",
                       f"Bash call with no readable command string: {str(inp)[:120]}",
                       recoverable=False)

    # --- RED: irreversible ----------------------------------------------------------------------
    # Scan the command with heredoc bodies removed. A heredoc body is data being handed to a
    # program -- Python source, SQL, prose -- not a command line, and scanning it produced 38
    # false REDs on accepted work from the word "shutdown" appearing inside Python.
    scan = _strip_heredocs(cmd) if cmd else ""
    if scan:
        for pat, rule in _RED_WHOLE_LINE:
            if pat.search(scan):
                return Finding(Band.RED, rule, f"{rule}: {scan[:160]}", recoverable=False)
        for seg in _segments(scan):
            for pat, rule in _RED_COMMAND:
                if pat.search(seg):
                    if rule == "rm-recursive-force":
                        targets = [_abs(x, grant.cwd) for x in _write_targets(tool, {"command": seg})]
                        if targets and all(_in_scratch(x, grant) for x in targets):
                            continue          # an agent tidying its own scratch
                    return Finding(Band.RED, rule, f"{rule}: {seg[:160]}", recoverable=False)

    # --- RED: protected paths, but only for writes, and only outside the grant ------------------
    # Scoped deliberately. A bare `endswith` over every path in the line made a repo's own test
    # fixture (`tests/fixtures/dummy.pem`) un-writable AND un-approvable, and even `shasum` on it
    # was RED. Reading a credential is not exfiltration, and blocking a repo's own files is the
    # fastest way to get the gate switched off.
    writes = tool in _WRITE_TOOLS or (tool == "Bash" and cmd and not _is_read_only_cmd(cmd))
    if writes:
        for raw in _mentioned_paths(tool, inp):
            p_abs = _abs(raw, grant.cwd)
            if _under(p_abs, grant.cwd) and not _protected_always(p_abs):
                continue                      # the project's own files are the project's business
            if (hit := _protected(p_abs)) is not None:
                return Finding(Band.RED, "protected-path", f"writes {hit}: {raw}", recoverable=False)

    if writes:
        for raw in _write_targets(tool, inp):
            if os.path.basename(_abs(raw, grant.cwd)) in _ASK_NAMES:
                return Finding(Band.YELLOW, "secret-bearing-file",
                               f"writes a file that usually holds credentials: {raw}",
                               recoverable=False)

    # --- tools that act outside the machine ------------------------------------------------------
    if tool not in _READ_TOOLS and tool not in _WRITE_TOOLS and tool not in _EGRESS_TOOLS \
            and tool != "Bash" and _TOOL_NAME_RED.search(tool):
        return Finding(Band.YELLOW, "outbound-tool",
                       f"{tool} acts outside this machine and was not granted", recoverable=False)

    # --- GREEN: reads are free, everywhere -------------------------------------------------------
    if tool in _READ_TOOLS:
        return GREEN_OK
    if cmd and _is_read_only_cmd(cmd):
        return GREEN_OK

    # --- writes: banded by recoverability, not by distance from the request ----------------------
    if tool in _WRITE_TOOLS or cmd:
        for raw in _write_targets(tool, inp):
            p_abs = _abs(raw, grant.cwd)
            if _in_scratch(p_abs, grant) or _harness_sanctioned(p_abs):
                continue
            if any(_under(p_abs, _abs(x, grant.cwd)) for x in grant.approved):
                continue                      # ratcheted: never ask twice
            if not _under(p_abs, grant.cwd):
                return Finding(Band.YELLOW, "write-outside-cwd",
                               f"touches a path outside the granted directory: {raw}",
                               recoverable=recoverable(p_abs, grant, st))
            if not recoverable(p_abs, grant, st):
                return Finding(Band.YELLOW, "unrecoverable-write",
                               f"not revertible from git: {raw}", recoverable=False)

    # --- YELLOW command families, per segment ----------------------------------------------------
    if scan:
        for seg in _segments(scan):
            for pat, rule in _YELLOW_COMMAND:
                if pat.search(seg):
                    return Finding(Band.YELLOW, rule, f"{rule}: {seg[:160]}", recoverable=True)

    return GREEN_OK


def _segments(cmd: str) -> list[str]:
    """Split a shell line into separately-classifiable commands.

    `_is_read_only_cmd` only ever looked at the first token, so any allowlisted binary in front
    laundered the whole line: `echo hi && rm -rf ~` was GREEN, and so was
    `echo <base64> | base64 -d | sh`. Splitting on `;`, `&&`, `||` and `|` means each part is
    judged on its own and the worst band wins.

    Deliberately naive about quoting: a `;` inside a quoted string produces an extra segment,
    which can only make the result MORE conservative, never less.
    """
    parts = re.split(r"\s*(?:\|\||&&|[;|&\n])\s*", cmd)
    return [s.strip() for s in parts if s.strip()]


_READ_ONLY_FIRST = frozenset({
    # Strictly non-mutating binaries only. Removed after the adversarial review of #60:
    #   find    -- `find . -delete` / `-exec rm` (now RED via _RED_COMMAND)
    #   python, python3, node, npx, make, sed, awk -- all execute arbitrary code and write
    #   go, cargo -- `go generate`, `cargo install`
    # A test runner is kept because refusing to recognise `pytest` makes the checker useless, but
    # note that a test suite can itself write: that is accepted, and is why writes are banded by
    # path independently of this list.
    "ls", "cat", "head", "tail", "grep", "rg", "wc", "diff", "stat", "file", "which",
    "pwd", "echo", "printf", "date", "env", "tree", "du", "df", "ps", "sort", "uniq",
    "pytest", "ruff", "mypy", "tsc", "eslint", "jest", "vitest", "shasum", "md5", "basename",
    "dirname", "realpath", "true", "false", "test",
})
# `config` writes (--global rewrites ~/.gitconfig) and `stash` moves the working tree out from
# under the user. Both were on this list; both are now YELLOW via _YELLOW_COMMAND.
_GIT_READ_ONLY = frozenset({"status", "log", "diff", "show", "branch", "remote", "rev-parse",
                            "ls-files", "blame", "describe", "cat-file", "shortlog"})


def _is_read_only_cmd(cmd: str) -> bool:
    """True only when EVERY segment is a recognised non-mutating command.

    A redirect turns any of these into a write (`ls > file`), so `>` disqualifies the line. Command
    substitution hides an arbitrary command inside an innocent one, so `$(` and a backtick do too.
    Being wrong here costs a needless YELLOW, not a missed RED.
    """
    if ">" in cmd or "$(" in cmd or "`" in cmd:
        return False
    segs = _segments(cmd)
    if not segs:
        return False
    for seg in segs:
        try:
            parts = shlex.split(seg)
        except ValueError:
            return False
        if not parts:
            return False
        head = os.path.basename(parts[0])
        if head == "git":
            sub = next((a for a in parts[1:] if not a.startswith("-")), "")
            if sub not in _GIT_READ_ONLY:
                return False
        elif head not in _READ_ONLY_FIRST:
            return False
    return True
