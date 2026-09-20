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

import fnmatch
import functools
import os
import re
import shlex
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .models import Claim, ClaimType, EventKind, LedgerEvent, Verdict, VerdictRecord
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


_POLICY_PATH = os.path.expanduser("~/.custos-code/policy.toml")


@dataclass(frozen=True)
class Policy:
    """User-widened or -narrowed scope rules, from `~/.custos-code/policy.toml` (SCOPE.md §6.4).

    Every rule in this module keeps working with no file at all: `Policy()` adds nothing and
    narrows nothing. Everything here is ADDITIVE -- a policy file can widen what counts as scratch
    or protected, but it cannot silently drop a built-in RED rule out from under a user who never
    asked for that; narrowing a built-in would need a code change and review, same as any other
    safety-critical default.

    `max_files_changed` is carried through but not yet consulted by `classify` -- SCOPE.md §7's
    calibration against the real corpus reported thresholds as UNSET at this sample size (9
    sessions vs. the ~400 the design calls for), and a magnitude rule that fires on a guess is the
    same mistake the whole scope gate ships OFF to avoid.
    """
    scratch: tuple[str, ...] = ()
    red: tuple[str, ...] = ()
    protect: tuple[str, ...] = ()
    max_files_changed: int = 0

    @staticmethod
    def load(path: str | None = None) -> Policy:
        p = path or _POLICY_PATH
        if not os.path.exists(p):
            return Policy()
        try:
            with open(p, "rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            return Policy()
        scope = data.get("scope")
        scope = scope if isinstance(scope, dict) else {}

        def _tup(key: str) -> tuple[str, ...]:
            v = scope.get(key)
            return tuple(str(x) for x in v) if isinstance(v, list) else ()

        max_files = scope.get("max_files_changed")
        return Policy(
            scratch=_tup("scratch"),
            red=_tup("red"),
            protect=_tup("protect"),
            max_files_changed=max_files if isinstance(max_files, int) else 0,
        )


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
    def for_session(cwd: str, named: tuple[str, ...] = (), approved: tuple[str, ...] = (),
                     policy: Policy | None = None) -> Grant:
        extra = [os.path.realpath(os.path.expandvars(os.path.expanduser(p)))
                 for p in (policy.scratch if policy else ())]
        scratch = [os.path.realpath(p) for p in
                   (os.environ.get("TMPDIR", "/tmp"), "/tmp", "/private/tmp",
                   os.path.expanduser("~/.custos-code"), *extra) if p]
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
    prefix test says it is. Same bug class as the CUSTOS_CODE_ONLY_IN fence in hooks.py.
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


def _protected(abs_path: str, extra: tuple[str, ...] = ()) -> str | None:
    for p in _PROTECTED:
        if _under(abs_path, os.path.expanduser(p)) or abs_path == os.path.expanduser(p):
            return p
    base = os.path.basename(abs_path)
    for suf in _PROTECTED_SUFFIX:
        if base == suf or base.endswith(suf):
            return suf
    for pat in extra:
        expanded = os.path.expanduser(pat)
        if fnmatch.fnmatch(abs_path, expanded) or fnmatch.fnmatch(base, expanded):
            return pat
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
    if _under(abs_path, grant.cwd) and (state.is_git or _git_root(abs_path) is not None):
        return True
    return False


def _paths_in(tool: str, inp: dict[str, Any]) -> list[str]:
    out = [v for k in ("file_path", "path", "notebook_path")
           if isinstance(v := inp.get(k), str) and v]
    if tool == "Bash" and isinstance(cmd := inp.get("command"), str):
        out += [q for t in _argument_tokens(cmd) if (q := _path_token(t))]
    return out

def _split_unquoted(cmd: str) -> list[str]:
    """Split on newlines and shell operators that are OUTSIDE quotes.

    `shlex` alone is not enough: it throws newlines away (so a multi-line block collapses into
    one segment and every program after the first line looks like an argument) and it keeps `;`
    glued to the preceding word. A raw `re.split` is not enough either: it cuts inside
    `ssh host 'cd x && ls'` and turns a remote path into a local one. This does both correctly.
    """
    out: list[str] = []
    buf: list[str] = []
    quote = ""
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            elif ch == "\\" and quote == '"' and i + 1 < len(cmd):
                buf.append(cmd[i + 1])
                i += 1
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch in "\n;|&":
            out.append("".join(buf))
            buf = []
            while i + 1 < len(cmd) and cmd[i + 1] in "\n;|&":
                i += 1
        else:
            buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [seg for seg in out if seg.strip()]


# --- shell-aware helpers (the nine calibration fixes) ------------------------------
# Each exists because a token that was never a path was being banded as a write, or
# text inside a heredoc was being read as a command. Measured over 298 real sessions
# and 23,826 tool calls of accepted work: 48.4% YELLOW -> 16.0%, no RED lost.

_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1(.*?)(?:^\2$|\Z)", re.S | re.M)
_FD_REDIR = re.compile(r"\d?>&\d?|\d?>\s*/dev/null")
_BOUNDARY = frozenset({"&&", "||", ";", "|", "&", "{", "}", "(", ")", "then", "do", "else"})
_NOT_A_PATH = re.compile(r"^\d*[<>]|://|\s|[$`*?|^\\\\\[\]]")
# Commands whose non-option arguments name the file being written, so a bare filename after a
# `cd` is a real target rather than an argument that happens to be a word.
_FILE_WRITERS = frozenset({"tee", "touch", "cp", "mv", "dd", "install", "ln", "truncate",
                           "patch", "sponge"})
_PREFIX_TOKENS = frozenset({"cd", "time", "timeout", "command", "exec", "nohup", "nice", "env"})
_ASSIGN = re.compile(r"(?:^|[;&|]|\n)\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=([^\s;&|<>]+)")
_VARREF = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
_CD = re.compile(r"""(?:^|[;&|]|\n)\s*cd\s+(?:--\s+)?(?:"([^"]*)"|'([^']*)'|([^\s;&|<>]+))""")
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "eval", "xargs", "source", ".",
                     "ssh", "env", "nohup", "timeout", "watch", "script"})


def _strip_heredocs(cmd: str) -> str:
    """Drop heredoc BODIES, keep the line that introduces them.

    A heredoc body is data handed to a program, not paths the shell touches. Leaving it in makes
    every slash-bearing token of an inlined Python/Markdown blob look like a write target. The
    redirection naming the real destination (`cat > f <<EOF`) is on the command line and survives.
    """
    return _HEREDOC.sub(lambda m: "<<" + m.group(2) + "\n", cmd)


def _segments(cmd: str) -> list[list[str]]:
    """Quote-aware split into pipeline segments, each a token list."""
    segs: list[list[str]] = []
    for raw in _split_unquoted(_expand_local_vars(_strip_heredocs(cmd))):
        try:
            toks = shlex.split(raw)
        except ValueError:
            toks = raw.split()
        if toks:
            segs.append(toks)
    return segs


def _strip_prefixes(parts: list[str]) -> list[str]:
    while parts:
        if "=" in parts[0] and not parts[0].startswith("="):
            parts = parts[1:]  # a VAR=value prefix is shell syntax, not the program
            continue
        head = os.path.basename(parts[0])
        if head in _PREFIX_TOKENS and head != "cd":
            parts = parts[1:]
            if head == "timeout" and parts and parts[0].replace(".", "").isdigit():
                parts = parts[1:]
            continue
        break
    return parts


def _segment_is_read_only(parts: list[str]) -> bool:
    """One pipeline segment. EVERY segment must pass, so this can only recognise more read
    shapes -- it can never launder a segment that mutates."""
    # An output redirection makes any command a write, however read-only the program is:
    # `ls > ~/.zshrc` does not read a shell config, it replaces one. Checked before the program
    # name, because the program name is exactly what makes this look harmless.
    #
    # `2>/dev/null` and `2>&1` are not writes to a user's file, and treating them as such is what
    # made `cat ~/.ssh/config 2>/dev/null` -- a pure read -- come back RED `protected-path`.
    for i, tok in enumerate(parts):
        if tok in (">", ">>") or (tok.startswith(">") and len(tok) > 1):
            target = parts[i + 1] if tok in (">", ">>") and i + 1 < len(parts) else tok.lstrip(">")
            if target and not target.startswith("/dev/"):
                return False
        elif _FD_REDIR.fullmatch(tok):
            continue
    parts = _strip_prefixes(parts)
    if not parts:
        return False
    head = os.path.basename(parts[0])
    if head == "cd":
        return len(parts) <= 2          # `cd X` on its own changes nothing on disk
    if head == "git":
        return len(parts) > 1 and parts[1] in _GIT_READ_ONLY
    return head in _READ_ONLY_FIRST


# ---- calibration variants ---------------------------------------------------------------------


def _is_shell_segment(parts: list[str]) -> bool:
    parts = _strip_prefixes(parts)
    return bool(parts) and os.path.basename(parts[0]) in _SHELLS


def _argument_tokens(cmd: str) -> list[str]:
    """Every token of every segment EXCEPT the word being executed.

    `.venv/bin/python build.py` runs the interpreter and writes nothing to it; banding argv[0] as
    a write target was the largest single source of false YELLOW. A path can only be written
    through a redirection or an argument, and both survive this.
    """
    out: list[str] = []
    for seg in _segments(cmd):
        parts = _strip_prefixes(seg)
        out += parts[1:] if parts else []
    return out


def _path_token(tok: str) -> str | None:
    """A shell token that could name a file on this machine.

    The old test -- any token containing a slash -- collected redirections (`2>/dev/null`), URLs,
    git refs (`origin/main`), repo slugs (`Olivesz/vitals`), sed programs and whole inlined
    scripts, and banded each as a write target. None of those is a path. Narrowing can only drop
    non-paths; whatever survives is banded exactly as before.
    """
    tok = tok.rstrip(";,")
    if not tok or tok.startswith("-") or _NOT_A_PATH.search(tok):
        return None
    if "=" in tok and not tok.startswith("="):
        tok = tok.split("=", 1)[1]                        # VAR=/path -- keep the path
    if not ("/" in tok or tok.startswith("~")):
        return None
    if re.fullmatch(r"[/.]+", tok) or tok.startswith("/dev/"):
        return None                                        # `/`, `./`, /dev/null: not real targets
    return tok


@functools.lru_cache(maxsize=8192)


def _git_root(start: str) -> str | None:
    """Nearest ancestor holding a .git entry. Filesystem stat only, no subprocess."""
    d = start if os.path.isdir(start) else os.path.dirname(start)
    prev = None
    while d and d != prev:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        prev, d = d, os.path.dirname(d)
    return None


def _effective_cwd(cmd: str, cwd: str) -> str:
    """`cd sub && python tests/x.py` puts `tests/x.py` under `sub`, not under the session root.

    Resolving against the session root invents a path that does not exist -- most of the
    `unrecoverable-write` volume -- and in the other direction MISSES the real target when the
    `cd` leaves the grant. An unexpandable `$VAR` aborts the walk rather than guessing.

    `_CD` has three alternative groups -- double-quoted, single-quoted, bare -- because a bare
    `[^\\s...]+` capture stops at the first space, and a path with a space in it is not a rare
    edge case: `cd "/Users/x/Documents/HackMIT 2026/receipts" && ...` is what every compound
    command in *this project's own checkout* looks like. Truncating the quoted target at that
    space silently rebased every subsequent path in the command onto a directory one level up
    that happens not to exist, which made `write-outside-cwd` fire on commands that never left
    the grant at all.
    """
    d = cwd
    for m in _CD.finditer(_strip_heredocs(cmd)):
        tgt = next(g for g in m.groups() if g is not None)
        if "$" in tgt or "`" in tgt:
            return d
        d = _abs(tgt, d)
    return d


def _expand_local_vars(cmd: str) -> str:
    """Substitute `VAR=value` assignments made EARLIER IN THE SAME command block.

    `SCRATCH=/tmp/.../scratchpad; rm -rf "$SCRATCH/build"` is an agent tidying up, and the whole
    point of the scratch exemption -- but with `$SCRATCH` unresolved the target is unknowable and
    the call is RED. This is pure string substitution over assignments visible in the same
    string: no shell runs, no environment is read, and a variable we cannot see stays unexpanded
    (and therefore stays conservative).
    """
    env = {m.group(1): m.group(2).strip("'\"") for m in _ASSIGN.finditer(cmd)}
    if not env:
        return cmd
    for _ in range(3):                                   # a value may itself mention a variable
        new = _VARREF.sub(lambda m: env.get(m.group(1), m.group(0)), cmd)
        if new == cmd:
            break
        cmd = new
    return cmd


def _scannable(cmd: str) -> str:
    """The text the RED/YELLOW command families are matched against.

    Two things in a command line are DATA, not shell: a heredoc body and a quoted multi-word
    argument. `git commit -m 'document pip install -e .[dev]'` is not a dependency install and
    `python3 - <<PY ... "rm -rf /etc" ... PY` is not a deletion -- matching shell patterns inside
    either is a category error, and between them they account for most of the surviving
    command-family volume.

    Both are kept verbatim whenever the program consuming them is a shell (`bash -c "..."`,
    `ssh host '...'`, a heredoc piped into `sh`), because then the data really is commands and
    dropping it would be a bypass.
    """
    parts_by_seg = _segments(cmd)
    if any(_is_shell_segment(p) for p in parts_by_seg):
        return cmd
    kept = [" ".join(t for t in parts if not re.search(r"\s", t)) for parts in parts_by_seg]
    kept = [k for k in kept if k]
    return " ; ".join(kept) if kept else _strip_heredocs(cmd)


def _rm_targets(cmd: str) -> list[str]:
    """Only what `rm` itself was pointed at.

    The exemption for a scratch cleanup asks whether EVERY target is disposable, so sweeping in
    every path-shaped token of a long command block -- the clone URL on the next line, the repo
    it then cd's into -- guarantees the answer is no. `rm -rf "$SCRATCH/build" && git clone ...`
    deletes exactly one thing.
    """
    out: list[str] = []
    for parts in _segments(cmd):
        for i, tok in enumerate(parts):
            if os.path.basename(tok) == "rm":
                out += [t for t in parts[i + 1:] if not t.startswith("-")]
                break
    return out


def _scratch_roots(grant: Grant) -> set[str]:
    return {os.path.realpath(s) for s in grant.scratch}

def classify(tool: str, tool_input: dict[str, Any], grant: Grant,
             state: RepoState | None = None, policy: Policy | None = None) -> Finding:
    """Band one tool call. Pure, deterministic, no model call.

    Order matters: RED first (an irreversible action is RED wherever it points), then reads (free),
    then writes and the YELLOW command families. The first match wins, so a `sudo rm -rf` reports
    as `rm-recursive-force` rather than as whichever rule happens to be checked last.

    `policy` widens the built-in bands (SCOPE.md §6.4); it never narrows one, so an empty or
    missing policy file classifies identically to no policy at all.
    """
    st = state if state is not None else RepoState(grant.cwd or None)
    pol = policy or Policy()
    inp = tool_input or {}
    raw_command = inp.get("command")
    cmd = raw_command if isinstance(raw_command, str) else ""
    # Relative paths resolve against the command's own `cd`, not the session cwd. Without this,
    # `cd sub && tee x` is banded as a write to <session>/x -- a different file -- and
    # `cd ~/.ssh && tee config` is missed entirely.
    base = _effective_cwd(cmd, grant.cwd) if cmd else grant.cwd

    # --- RED: irreversible, wherever it points -------------------------------------------------
    if tool == "Bash" and cmd:
        for pat, rule in _RED_COMMAND:
            if pat.search(_scannable(cmd)):
                # An rm -rf confined to scratch is how agents clean up after themselves.
                if rule == "rm-recursive-force":
                    targets = [_abs(q, base) for t in _rm_targets(cmd) if (q := _path_token(t))]
                    if targets and all(_in_scratch(t, grant) for t in targets):
                        return GREEN_OK
                return Finding(Band.RED, rule, f"{rule}: {cmd[:160]}", recoverable=False)
        for phrase in pol.red:
            if phrase and phrase.lower() in cmd.lower():
                return Finding(Band.RED, "policy-red", f"policy-red: {cmd[:160]}", recoverable=False)

    if tool in _WRITE_TOOLS or (tool == "Bash" and cmd):
        for raw in _write_relevant_paths(tool, inp, cmd):
            p = _abs(raw, base)
            if hit := _protected(p, pol.protect):
                return Finding(Band.RED, "protected-path", f"writes {hit}: {raw}", recoverable=False)

    # --- GREEN: reads are free, everywhere -----------------------------------------------------
    if tool in _READ_TOOLS:
        return GREEN_OK
    if tool == "Bash" and cmd and _is_read_only_cmd(cmd):
        return GREEN_OK

    # --- writes: banded by recoverability, not by distance from the request --------------------
    if tool in _WRITE_TOOLS or (tool == "Bash" and cmd):
        for raw in _write_relevant_paths(tool, inp, cmd):
            p = _abs(raw, base)
            if _in_scratch(p, grant):
                continue
            if any(_under(p, _abs(a, base)) for a in grant.approved):
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
            if pat.search(_scannable(cmd)):
                return Finding(Band.YELLOW, rule, f"{rule}: {cmd[:160]}", recoverable=True)

    return GREEN_OK


_READ_ONLY_FIRST = frozenset({
    "ls", "cat", "head", "tail", "grep", "rg", "find", "wc", "diff", "stat", "file", "which",
    "pwd", "echo", "printf", "date", "env", "tree", "du", "df", "ps", "sed", "awk", "sort", "uniq",
    "pytest", "npx", "node", "python", "python3", "go", "cargo", "make", "ruff", "mypy", "tsc",
    "eslint", "jest", "vitest",
    # Shell scaffolding: these never touch a file on disk themselves, so they cost nothing to
    # allow -- and without them, ANY multi-step command wrapped in `cd project && ...` or
    # `source ~/.env && ...` fell through as "unrecognised" on the first segment alone (the old,
    # single-token check), which is what let a plain `source ~/.env` get reported as "writes .env".
    "cd", "source", ".", "set", "export", "unset",
})
_GIT_READ_ONLY = frozenset({"status", "log", "diff", "show", "branch", "remote", "rev-parse",
                            "ls-files", "blame", "describe", "config", "stash"})


def _is_read_only_cmd(cmd: str) -> bool:
    """Conservative: only commands we recognise as non-mutating, and only without a redirect.

    A redirect turns any of these into a write (`ls > file`), so the presence of `>` disqualifies
    the whole line. Being wrong here costs a needless YELLOW, not a missed RED.
    """
    if ">" in _FD_REDIR.sub("", _strip_heredocs(cmd)):
        return False
    segs = _segments(cmd)
    return bool(segs) and all(_segment_is_read_only(p) for p in segs)

def _write_relevant_paths(tool: str, inp: dict[str, Any], cmd: str) -> list[str]:
    """Path-like arguments worth banding as a potential write.

    For every tool but Bash this is just `_paths_in` -- an Edit/Write call's `file_path` is
    always the thing being written. For Bash, a path mentioned only inside a segment recognised
    as read-only is not a write candidate at all: `source ~/.env && uv run ...` does not write
    `.env`, whatever the rest of the line goes on to do, and treating "the line has an
    unrecognised step somewhere" as "every path in the line might be written" is what made that
    read get reported as a write in the first place. A path inside a segment we don't recognise
    still counts, same as before -- this narrows false positives, it does not loosen real ones.
    """
    if tool != "Bash":
        return _paths_in(tool, inp)
    out: list[str] = []
    # `_segments` strips heredoc bodies and respects quoting; `_path_token` rejects tokens that
    # were never paths. Between them these drop the largest sources of false writes measured over
    # 23,826 real calls: `.venv/bin/python` (the interpreter, argv[0]) 900 times, `2>/dev/null`
    # 310, plus sed programs, `origin/main`, URLs and whole `VAR=value` words.
    for parts in _segments(cmd):
        if _segment_is_read_only(parts):
            continue
        args = _strip_prefixes(parts)
        out += [q for t in args[1:] if (q := _path_token(t))]
        # A bare token with no slash is normally not a path -- `pytest -q`, `git status`. But
        # after a `cd`, the file a writer is pointed at usually IS bare: `cd ~/.ssh && tee
        # config` writes ~/.ssh/config, and rejecting `config` for having no slash is how that
        # became invisible. Restricted to commands whose argument is the file they write, so it
        # cannot start treating every flag value on every line as a path.
        if args and os.path.basename(args[0]) in _FILE_WRITERS:
            out += [t for t in args[1:]
                    if t and not t.startswith("-") and _path_token(t) is None
                    and not _NOT_A_PATH.search(t)]
    return out


# ---------------------------------------------------------------------------------------------
# Reaching the receipt (issue #64). `PreToolUse` (hooks.py) decides ask/deny before an action
# runs and never itself writes to the ledger -- a denied call leaves no CALL event to report on,
# by design. But plenty of gated actions DO end up in the ledger anyway: warn mode never blocks,
# an attended "ask" the user approved still ran, and a class-R bundle (Copilot, Devin) has no live
# gate in front of it at all. `scan` is how those reach a receipt post-hoc, by re-running the same
# pure `classify` over the CALL events a session already recorded.
# ---------------------------------------------------------------------------------------------

def scan(ledger: Sequence[LedgerEvent], grant: Grant, state: RepoState | None = None,
         policy: Policy | None = None) -> list[tuple[LedgerEvent, Finding]]:
    """Every top-level tool call that would have gated, paired with the event that made it.

    Sidechain (sub-agent) calls are excluded -- the same rule integrity already applies to
    evidence: a sub-agent's actions have no write path either checker treats as the top-level
    agent's own.
    """
    st = state if state is not None else RepoState(grant.cwd or None)
    hits: list[tuple[LedgerEvent, Finding]] = []
    for event in ledger:
        if event.kind is not EventKind.CALL or event.flags.sidechain or not event.tool:
            continue
        finding = classify(event.tool, event.input or {}, grant, st, policy)
        if finding.gates:
            hits.append((event, finding))
    return hits


def to_verdict(event: LedgerEvent, finding: Finding) -> tuple[Claim, VerdictRecord]:
    """One scope hit as a (Claim, VerdictRecord) pair, so report.py renders it like any other row.

    Deliberately `out_of_scope`, never `contradicted` -- SCOPE.md §4: this is an action against a
    boundary, not positive evidence a claim is false, and `contradicted` is reserved for that.
    """
    claim = Claim(
        id=f"scope-{event.seq}",
        session_id=event.session_id,
        text=f"{event.tool} at #{event.seq}: {finding.detail}",
        type=ClaimType.OTHER,
        polarity="did",
        source="scope",
    )
    record = VerdictRecord(
        claim_id=claim.id,
        verdict=Verdict.OUT_OF_SCOPE,
        tier=2,
        method="rule",
        confidence=1.0,
        evidence=[event.seq],
        rationale=f"[{finding.band.value}] {finding.rule}: {finding.detail}",
        band=finding.band.value,
    )
    return claim, record
