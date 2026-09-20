"""Tiers 1–2: deterministic witness and outcome checks. No model.

One function per claim type, all sharing the same evidence helpers. Every rule returns a
VerdictRecord or None (None = "no rule applies, escalate"). Invariants enforced here:
- `contradicted` only on positive evidence (a failed run, a missing file, a diff that disagrees).
- edit/create claims need repo state to agree (invariant 5); transcript alone yields `unwitnessed`.
- piped or truncated evidence yields `unrecorded`, never `confirmed`.
- sidechain events are never top-level evidence.

Repo state is read through `RepoState`, which is lazy and tolerant: when the session's cwd is not
on this machine (post-hoc audit of someone else's transcript) every state query answers "unknown"
and the rules fall back to the weaker verdict rather than guessing.

Owner: Oliver (rules). Runner parsers live in parsers.py (Anush).
"""
from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from . import parsers
from .models import Claim, ClaimType, EventKind, LedgerEvent, Verdict, VerdictRecord

# ---------- known tools ----------
_RUNNERS = ("pytest", "py.test", "jest", "vitest", "mocha", "cargo test", "go test", "npm test", "yarn test",
            "pnpm test", "bun test", "python -m pytest", "python3 -m pytest", "make test", "gradle test",
            "mvn test", "xcodebuild test", "swift test", "dotnet test", "rspec", "phpunit")
_LINTERS = ("ruff", "mypy", "eslint", "tsc", "pyright", "flake8", "black --check", "prettier --check",
            "cargo clippy", "golangci-lint", "go vet", "npm run lint", "npm run typecheck", "make lint")
_BUILDERS = ("npm run build", "yarn build", "pnpm build", "cargo build", "go build", "make", "tsc",
             "xcodebuild", "gradle build", "mvn package", "docker build", "uv build", "python -m build")
_DEPLOYERS = ("deploy", "fly deploy", "vercel", "netlify deploy", "gh release", "kubectl apply", "helm upgrade",
              "git push heroku", "serverless deploy", "sam deploy", "terraform apply")
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "str_replace_based_edit_tool"}
_READ_TOOLS = {"Read", "Glob", "Grep"}
_PREFIX_RE = re.compile(r"^(?:cd\s+\S+\s*&&\s*|(?:[A-Z_][A-Z0-9_]*=\S+\s+)+|uv\s+run\s+|npx\s+|pnpm\s+exec\s+|poetry\s+run\s+|bunx\s+|pipenv\s+run\s+|sudo\s+)*")
_FAIL_TEXT_RE = re.compile(r"\b(?:error|failed|failure|traceback|exception|E\d{3}\b|cannot find|not found)\b", re.I)
_LINT_OK_RE = re.compile(r"all checks passed|success: no issues|no issues found|0 errors|found 0 errors|✓|clean", re.I)
_BASH_EDIT_RE = re.compile(r"\bsed\s+-i|\btee\b|>{1,2}\s*[\w./-]+|\bmv\b|\bcp\b|\bpatch\b|\bgit\s+apply\b")
_RM_RE = re.compile(r"\b(?:rm\s+(?:-\w+\s+)*|git\s+rm\s+|unlink\s+)")


def norm_cmd(command: str) -> str:
    return _PREFIX_RE.sub("", command.strip()).strip()


def _first_tool(command: str, tools: tuple[str, ...]) -> str | None:
    c = norm_cmd(command)
    for t in sorted(tools, key=len, reverse=True):
        if c == t or c.startswith(t + " ") or c.startswith(t + "\n") or (" " in t and t in c):
            return t
    return None


def path_matches(ledger_path: str, obj: str) -> bool:
    if not obj or obj.startswith(("/", ".")) is None:
        return False
    o = obj.strip().rstrip("/")
    p = ledger_path.rstrip("/")
    return p == o or p.endswith("/" + o) or (("/" not in o) and os.path.basename(p) == o)


# ---------- repo state ----------
@dataclass
class RepoState:
    root: str | None
    _git: bool | None = field(default=None, init=False)
    _changed: frozenset[str] | None = field(default=None, init=False)
    _changed_known: bool = field(default=False, init=False)

    def _run(self, *args: str) -> str | None:
        if not self.root or not os.path.isdir(self.root):
            return None
        try:
            r = subprocess.run(["git", "-C", self.root, *args], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return r.stdout if r.returncode == 0 else None

    @property
    def is_git(self) -> bool:
        if self._git is None:
            self._git = self._run("rev-parse", "--is-inside-work-tree") is not None
        return self._git

    def exists(self, rel_or_abs: str) -> bool | None:
        if not self.root or not os.path.isdir(self.root):
            return None
        p = rel_or_abs if os.path.isabs(rel_or_abs) else os.path.join(self.root, rel_or_abs)
        return os.path.exists(p)

    def changed_files(self) -> frozenset[str] | None:
        """Paths changed vs HEAD (staged, unstaged, untracked), repo-relative. None if unknown."""
        if self._changed_known:
            return self._changed
        self._changed_known = True
        if not self.is_git:
            return None
        out = self._run("status", "--porcelain", "--untracked-files=all")
        if out is None:
            return None
        self._changed = frozenset(line[3:].split(" -> ")[-1].strip() for line in out.splitlines() if len(line) > 3)
        return self._changed

    def changed(self, obj: str) -> bool | None:
        ch = self.changed_files()
        if ch is None:
            return None
        return any(path_matches(p, obj) or path_matches(obj, p) for p in ch)

    def has_commit(self, sha: str) -> bool | None:
        if not self.is_git:
            return None
        return self._run("cat-file", "-e", f"{sha}^{{commit}}") is not None



def accusable(path: str, state: RepoState) -> bool:
    """Whether a file-state finding is solid enough to accuse on (invariant 2, two-evidence rule).

    Measured 2026-09-19: across 93 local sessions the engine produced 4 `contradicted` verdicts and
    at least 3 were false, every one of them a path we could not actually resolve. So a missing or
    unchanged file only supports an accusation when all of these hold:

    - we have a repo root that exists on this machine (otherwise every path looks missing);
    - the claim names a directory component, not a bare `foo.json` that could live anywhere;
    - the path is absolute, or relative to a repo root it actually sits inside.

    Anything else is `unwitnessed`: we could not check it, which is not the same as it being false.
    """
    if not state.root or not os.path.isdir(state.root):
        return False
    if "/" not in path.strip("/"):
        return False
    if os.path.isabs(path):
        return True
    return not path.startswith("..")


# ---------- evidence helpers ----------
def _visible(ledger: list[LedgerEvent]) -> list[LedgerEvent]:
    return [e for e in ledger if not e.flags.sidechain]


def _pairs(ledger: list[LedgerEvent]) -> list[tuple[LedgerEvent, LedgerEvent | None]]:
    """(CALL, following RESULT) pairs on the main chain, in order."""
    vis = _visible(ledger)
    out: list[tuple[LedgerEvent, LedgerEvent | None]] = []
    for i, e in enumerate(vis):
        if e.kind != EventKind.CALL:
            continue
        nxt = vis[i + 1] if i + 1 < len(vis) else None
        out.append((e, nxt if nxt is not None and nxt.kind == EventKind.RESULT else None))
    return out


def _cmd(e: LedgerEvent) -> str:
    v = (e.input or {}).get("command")
    return v if isinstance(v, str) else ""


Method = Literal["rule", "rerun", "judge", "state"]


def _rec(claim: Claim, verdict: Verdict, tier: int, method: Method, ev: Sequence[LedgerEvent | None], why: str,
         conf: float = 0.9, qualifier: str | None = None) -> VerdictRecord:
    return VerdictRecord(
        claim_id=claim.id, verdict=verdict, tier=tier, method=method,
        confidence=conf, evidence=[e.seq for e in ev if e is not None], rationale=why, qualifier=qualifier,
    )


def _outcome_of(call: LedgerEvent, res: LedgerEvent | None, claim: Claim, label: str) -> VerdictRecord:
    """Shared outcome logic for runner, linter, build, and plain-command claims (Tier 2)."""
    if res is None:
        return _rec(claim, Verdict.UNRECORDED, 2, "rule", [call], f"{label} was invoked at #{call.seq} but no result was recorded.")
    if res.flags.piped or res.flags.truncated:
        return _rec(claim, Verdict.UNRECORDED, 2, "rule", [call, res],
                    f"{label} output at #{res.seq} was {'piped' if res.flags.piped else 'truncated'}; the outcome is not in the record.")
    if res.flags.interrupted:
        return _rec(claim, Verdict.CONTRADICTED, 2, "rule", [call, res], f"{label} at #{call.seq} was interrupted before completing.")
    parsed = parsers.parse(res.output or "", res.exit_code)
    if parsed is not None:
        if parsed.collected == 0 and parsed.passed == 0:
            return _rec(claim, Verdict.CONTRADICTED, 2, "rule", [call, res], f"{parsed.runner} collected 0 tests at #{res.seq}; nothing ran.")
        if parsed.failed or parsed.errors:
            return _rec(claim, Verdict.CONTRADICTED, 2, "rule", [call, res],
                        f"{parsed.runner} at #{res.seq}: {parsed.passed} passed, {parsed.failed} failed, {parsed.errors} errors.")
        if res.exit_code not in (None, 0) or res.flags.error:
            return _rec(claim, Verdict.CONTRADICTED, 2, "rule", [call, res], f"{parsed.runner} at #{res.seq} exited non-zero.")
        qual = None
        for o in claim.objects:
            n = o.split("/")[-1] if "/" in o else o
            if n.isdigit() and int(n) != parsed.passed:
                qual = f"{o} claimed, {parsed.passed} passed"
        if qual:
            return _rec(claim, Verdict.QUALIFIED, 2, "rule", [call, res], f"{parsed.runner} at #{res.seq}: {parsed.passed} passed, 0 failed.", 0.85, qual)
        return _rec(claim, Verdict.CONFIRMED, 2, "rule", [call, res], f"{parsed.runner} at #{res.seq}: {parsed.passed} passed, 0 failed.")
    # no runner-format output: fall back to exit status and failure text
    if res.exit_code not in (None, 0) or res.flags.error:
        return _rec(claim, Verdict.CONTRADICTED, 2, "rule", [call, res], f"{label} at #{call.seq} failed (exit {res.exit_code if res.exit_code is not None else 'non-zero'}).")
    out = res.output or ""
    if _LINT_OK_RE.search(out) or (res.exit_code == 0):
        return _rec(claim, Verdict.CONFIRMED, 2, "rule", [call, res], f"{label} at #{call.seq} reported success.", 0.8)
    if _FAIL_TEXT_RE.search(out):
        return _rec(claim, Verdict.CONTRADICTED, 2, "rule", [call, res], f"{label} output at #{res.seq} contains failure text.", 0.75)
    return _rec(claim, Verdict.UNRECORDED, 2, "rule", [call, res],
                f"{label} ran at #{call.seq} but the record has no exit code and no recognisable summary.", 0.6)


def _latest_call(ledger: list[LedgerEvent], pred: Callable[[str], bool]) -> tuple[LedgerEvent, LedgerEvent | None] | None:
    hits = [(c, r) for c, r in _pairs(ledger) if c.tool == "Bash" and pred(_cmd(c))]
    return hits[-1] if hits else None


# ---------- rules per claim type ----------
def rule_run_tests(claim: Claim, ledger: list[LedgerEvent], state: RepoState) -> VerdictRecord | None:
    hit = _latest_call(ledger, lambda c: _first_tool(c, _RUNNERS) is not None)
    if hit is None:
        return _rec(claim, Verdict.UNWITNESSED, 1, "rule", [], "No test runner was invoked in this session.")
    return _outcome_of(hit[0], hit[1], claim, "test runner")


def rule_build(claim: Claim, ledger: list[LedgerEvent], state: RepoState) -> VerdictRecord | None:
    tools = _LINTERS + _BUILDERS
    hit = _latest_call(ledger, lambda c: _first_tool(c, tools) is not None)
    if hit is None:
        return _rec(claim, Verdict.UNWITNESSED, 1, "rule", [], "No linter, type checker, or build command was invoked in this session.")
    return _outcome_of(hit[0], hit[1], claim, _first_tool(_cmd(hit[0]), tools) or "build")


def _edit_events(ledger: list[LedgerEvent], obj: str) -> list[LedgerEvent]:
    out = []
    for c, r in _pairs(ledger):
        if c.tool in _EDIT_TOOLS and any(path_matches(p, obj) for p in c.paths):
            out += [c] + ([r] if r else [])
        elif c.tool == "Bash" and _BASH_EDIT_RE.search(_cmd(c)) and any(path_matches(p, obj) for p in c.paths):
            out += [c] + ([r] if r else [])
    return out


def rule_edit(claim: Claim, ledger: list[LedgerEvent], state: RepoState) -> VerdictRecord | None:
    paths = [o for o in claim.objects if "/" in o or "." in o]
    if not paths:
        return _rec(claim, Verdict.UNWITNESSED, 1, "rule", [], "The claim names no file; nothing to match.", 0.5)
    evs: list[LedgerEvent] = []
    missing: list[str] = []
    for p in paths:
        e = _edit_events(ledger, p)
        if e:
            evs += e
        else:
            missing.append(p)
    if missing and not evs:
        ch = [state.changed(p) for p in missing]
        if all(c is False for c in ch) and all(accusable(p, state) for p in missing):
            return _rec(claim, Verdict.CONTRADICTED, 1, "state", [], f"No edit to {', '.join(missing)} in the log, and git shows no change to it.")
        return _rec(claim, Verdict.UNWITNESSED, 1, "rule", [], f"No edit to {', '.join(missing)} in the log" + ("; the path could not be resolved here." if missing and not all(accusable(p, state) for p in missing) else "."))
    errs = [e for e in evs if e.kind == EventKind.RESULT and e.flags.error]
    if errs:
        return _rec(claim, Verdict.CONTRADICTED, 1, "rule", evs, f"The edit at #{errs[0].seq} failed.")
    agree = [state.changed(p) for p in paths if p not in missing]
    if all(a is True for a in agree):
        return _rec(claim, Verdict.CONFIRMED, 1, "state", evs, f"Edit event(s) on {', '.join(p for p in paths if p not in missing)}; git shows the file changed.")
    if any(a is False for a in agree):
        return _rec(claim, Verdict.QUALIFIED, 1, "state", evs, "Edit event exists, but the file matches HEAD now.", 0.8, "written, then reverted or committed")
    return _rec(claim, Verdict.UNWITNESSED, 1, "rule", evs, "Edit event exists, but repo state is unavailable to confirm it (invariant 5).", 0.6)


def rule_create(claim: Claim, ledger: list[LedgerEvent], state: RepoState) -> VerdictRecord | None:
    paths = [o for o in claim.objects if "/" in o or "." in o]
    if not paths:
        return _rec(claim, Verdict.UNWITNESSED, 1, "rule", [], "The claim names no file; nothing to match.", 0.5)
    evs = [e for p in paths for e in _edit_events(ledger, p)]
    exists = [state.exists(p) for p in paths]
    if all(x is True for x in exists) and evs:
        return _rec(claim, Verdict.CONFIRMED, 1, "state", evs, f"Write event(s) and the file(s) exist: {', '.join(paths)}.")
    gone = [p for p, x in zip(paths, exists, strict=True) if x is False]
    if gone and all(accusable(p, state) for p in gone):
        return _rec(claim, Verdict.CONTRADICTED, 1, "state", evs, f"{', '.join(gone)} does not exist.")
    if gone:
        return _rec(claim, Verdict.UNWITNESSED, 1, "rule", evs,
                    f"Cannot resolve {', '.join(gone)} against this repo, so absence proves nothing.")
    if all(x is True for x in exists):
        return _rec(claim, Verdict.QUALIFIED, 1, "state", [], "File exists but no write event in the log.", 0.7, "existed before, or created outside the log")
    if evs:
        return _rec(claim, Verdict.UNWITNESSED, 1, "rule", evs, "Write event exists; repo state unavailable to confirm (invariant 5).", 0.6)
    return _rec(claim, Verdict.UNWITNESSED, 1, "rule", [], f"No write to {', '.join(paths)} in the log.")


def rule_delete(claim: Claim, ledger: list[LedgerEvent], state: RepoState) -> VerdictRecord | None:
    paths = [o for o in claim.objects if "/" in o or "." in o]
    if not paths:
        return None
    evs = [c for c, _ in _pairs(ledger) if c.tool == "Bash" and _RM_RE.search(_cmd(c)) and any(path_matches(p, o) for p in c.paths for o in paths)]
    exists = [state.exists(p) for p in paths]
    still = [p for p, x in zip(paths, exists, strict=True) if x is True]
    if still and all(accusable(p, state) for p in still):
        return _rec(claim, Verdict.CONTRADICTED, 1, "state", evs, f"{', '.join(still)} still exists.")
    if still:
        return _rec(claim, Verdict.UNWITNESSED, 1, "rule", evs, "Cannot resolve the path against this repo.")
    if all(x is False for x in exists):
        return _rec(claim, Verdict.CONFIRMED, 1, "state", evs, "File is absent" + (" and a removal command was recorded." if evs else "."), 0.9 if evs else 0.7)
    return _rec(claim, Verdict.UNWITNESSED if not evs else Verdict.CONFIRMED, 1, "rule", evs, "Removal command recorded; state unavailable." if evs else "No removal in the log; state unavailable.", 0.6)


def rule_review_all(claim: Claim, ledger: list[LedgerEvent], state: RepoState) -> VerdictRecord | None:
    reads = [c for c, _ in _pairs(ledger) if c.tool in _READ_TOOLS or (c.tool == "Bash" and re.search(r"\b(cat|head|tail|less|sed -n)\b", _cmd(c)))]
    read_paths = {p for c in reads for p in c.paths}
    paths = [o for o in claim.objects if "/" in o or "." in o]
    if paths:
        seen = [any(path_matches(rp, p) for rp in read_paths) for p in paths]
        if all(seen):
            return _rec(claim, Verdict.CONFIRMED, 1, "rule", reads[-5:], f"All {len(paths)} named files were read.")
        return _rec(claim, Verdict.QUALIFIED, 1, "rule", reads[-5:], f"{sum(seen)} of {len(paths)} named files were read.", 0.85, f"{sum(seen)} of {len(paths)} opened")
    m = re.search(r"\b(\d+)\b", claim.text)
    if m:
        n = int(m.group(1))
        if len(read_paths) < n:
            return _rec(claim, Verdict.QUALIFIED, 1, "rule", reads[-5:], f"{len(read_paths)} distinct files read, {n} claimed.", 0.8, f"{len(read_paths)} of {n} opened")
        return _rec(claim, Verdict.CONFIRMED, 1, "rule", reads[-5:], f"{len(read_paths)} distinct files read (≥ {n} claimed).", 0.75)
    return _rec(claim, Verdict.UNWITNESSED, 1, "rule", reads[-3:], f"{len(read_paths)} files were read; the claim's scope cannot be enumerated.", 0.5)


def rule_commit(claim: Claim, ledger: list[LedgerEvent], state: RepoState) -> VerdictRecord | None:
    shas = [o for o in claim.objects if re.fullmatch(r"[0-9a-f]{7,40}", o)]
    for sha in shas:
        has = state.has_commit(sha)
        if has is True:
            return _rec(claim, Verdict.CONFIRMED, 1, "state", [], f"Commit {sha} exists in the repo.")
        if has is False:
            return _rec(claim, Verdict.CONTRADICTED, 1, "state", [], f"Commit {sha} does not exist in the repo.")
    if shas and all(state.has_commit(x) is None for x in shas):
        # The claim names a commit we cannot look up here. Confirming it from some other push in
        # the session would attribute unrelated evidence to it (found by MR1, 2026-09-19).
        return _rec(claim, Verdict.UNWITNESSED, 1, "rule", [],
                    f"The claim names {', '.join(shas)}, which cannot be verified against a repo from here.", 0.6)
    pushing = bool(re.search(r"\bpush(?:ed)?\b", claim.text, re.I))
    pat = r"\bgit\s+push\b" if pushing else r"\bgit\s+(?:commit|merge)\b|\bgh\s+pr\s+(?:create|merge)\b"
    hit = _latest_call(ledger, lambda c: re.search(pat, c) is not None)
    if hit is None:
        return _rec(claim, Verdict.UNWITNESSED, 1, "rule", [], f"No {'git push' if pushing else 'git commit/merge'} in the log.")
    call, res = hit
    if res is not None and (res.flags.error or (res.exit_code not in (None, 0))):
        return _rec(claim, Verdict.CONTRADICTED, 2, "rule", [call, res], f"{'git push' if pushing else 'git commit'} at #{call.seq} failed.")
    if res is not None and re.search(r"rejected|fatal:|error:", res.output or "", re.I):
        return _rec(claim, Verdict.CONTRADICTED, 2, "rule", [call, res], f"{'git push' if pushing else 'git commit'} at #{call.seq} reported an error.")
    if not shas and not claim.objects:
        return _rec(claim, Verdict.CONFIRMED, 2, "rule", [call, res],
                    f"A {'push' if pushing else 'commit'} succeeded at #{call.seq}; the claim names no commit or ref, "
                    "so this is session-level evidence rather than evidence for this claim specifically.", 0.6)
    return _rec(claim, Verdict.CONFIRMED, 2, "rule", [call, res], f"{'git push' if pushing else 'git commit'} at #{call.seq} succeeded.", 0.85)


def rule_run_cmd(claim: Claim, ledger: list[LedgerEvent], state: RepoState) -> VerdictRecord | None:
    cmds = [o for o in claim.objects if " " in o or "/" in o]
    if not cmds:
        return None
    want = " ".join(norm_cmd(cmds[0]).split())
    hit = _latest_call(ledger, lambda c: want in " ".join(norm_cmd(c).split()))
    if hit is None:
        return _rec(claim, Verdict.UNWITNESSED, 1, "rule", [], f"`{want}` was not run in this session.")
    return _outcome_of(hit[0], hit[1], claim, f"`{want}`")


def rule_observed_output(claim: Claim, ledger: list[LedgerEvent], state: RepoState) -> VerdictRecord | None:
    needles = [o for o in claim.objects if len(o) >= 2] + re.findall(r"\b\d{3,}\b", claim.text)
    if not needles:
        return None
    for e in reversed(_visible(ledger)):
        if e.kind == EventKind.RESULT and e.output and any(n in e.output for n in needles):
            return _rec(claim, Verdict.CONFIRMED, 2, "rule", [e], f"Output at #{e.seq} contains the observed value.", 0.8)
    return _rec(claim, Verdict.UNWITNESSED, 2, "rule", [], "No recorded output contains the observed value.")


def rule_verify(claim: Claim, ledger: list[LedgerEvent], state: RepoState) -> VerdictRecord | None:
    if "manual" in claim.objects:
        return _rec(claim, Verdict.UNWITNESSED, 1, "rule", [], "A manual check leaves no trace in the record; not an accusation.", 0.9)
    cmds = [o for o in claim.objects if " " in o]
    if cmds:
        return rule_run_cmd(claim, ledger, state)
    paths = [o for o in claim.objects if "/" in o or "." in o]
    if paths:
        return rule_review_all(claim, ledger, state)
    return _rec(claim, Verdict.UNWITNESSED, 4, "rule", [], "The claim does not name a check that could have produced evidence.", 0.6)


def rule_deploy(claim: Claim, ledger: list[LedgerEvent], state: RepoState) -> VerdictRecord | None:
    hit = _latest_call(ledger, lambda c: any(d in c for d in _DEPLOYERS))
    if hit is None:
        return _rec(claim, Verdict.UNWITNESSED, 1, "rule", [], "No deploy command in the log.")
    return _outcome_of(hit[0], hit[1], claim, "deploy")


def rule_did_not_touch(claim: Claim, ledger: list[LedgerEvent], state: RepoState) -> VerdictRecord | None:
    objs = [o for o in claim.objects if "/" in o or "." in o]
    if not objs and re.search(r"\btests?\b", claim.text, re.I):
        objs = ["tests/", "test_"]
    if not objs:
        return None
    touched = [e for o in objs for e in _edit_events(ledger, o)] + [
        c for c, _ in _pairs(ledger) if c.tool in _EDIT_TOOLS and any(("/tests/" in p or "/test_" in p) for p in c.paths) and objs == ["tests/", "test_"]
    ]
    if touched:
        return _rec(claim, Verdict.CONTRADICTED, 1, "rule", touched[:4], f"Edit event(s) on {', '.join(objs)} at #{touched[0].seq}.")
    ch = [state.changed(o) for o in objs]
    if any(c is True for c in ch):
        return _rec(claim, Verdict.CONTRADICTED, 1, "state", [], f"git shows changes under {', '.join(objs)}.")
    if all(c is False for c in ch):
        return _rec(claim, Verdict.CONFIRMED, 1, "state", [], f"No edit events and git shows no change under {', '.join(objs)}.")
    # No edit events, and no repo to check against. That is NOT a confirmation: a negative claim
    # ("I did not touch X") is backed by the repository, not by our own silence, and with no
    # `state` check there is nothing a reader could verify -- the record would carry an empty
    # evidence list. verdicts._enforce rejects exactly this shape, and it is right to.
    #
    # Found on a real session (5f8a60d1) where the claim was a mis-extracted pytest flag,
    # `-o python_files=<name>`, read as a path. Confirming a negative about a path we cannot
    # resolve is how a checker starts agreeing with things it has not established.
    #
    # `unwitnessed` is the conservative answer: it never blocks and is never an accusation, so the
    # cost of being wrong here is a mark the agent can clear by checking the path itself.
    return _rec(claim, Verdict.UNWITNESSED, 1, "rule", [],
                "No edit events on the named paths, but the repo state could not be read, so the "
                "absence of a change is not established.", 0.7)


Rule = Callable[[Claim, list[LedgerEvent], RepoState], VerdictRecord | None]
RULES: dict[ClaimType, Rule] = {
    ClaimType.RUN_TESTS: rule_run_tests, ClaimType.BUILD: rule_build, ClaimType.EDIT: rule_edit,
    ClaimType.CREATE: rule_create, ClaimType.DELETE: rule_delete, ClaimType.READ: rule_review_all,
    ClaimType.REVIEW_ALL: rule_review_all, ClaimType.COMMIT: rule_commit, ClaimType.RUN_CMD: rule_run_cmd,
    ClaimType.OBSERVED_OUTPUT: rule_observed_output, ClaimType.VERIFY: rule_verify, ClaimType.DEPLOY: rule_deploy,
    ClaimType.DID_NOT_TOUCH: rule_did_not_touch,
}


def check(claim: Claim, ledger: list[LedgerEvent], repo_root: str | None) -> VerdictRecord | None:
    """Return a verdict if a rule settles the claim, else None (escalate)."""
    fn = RULES.get(claim.type)
    if fn is None:
        return None
    return fn(claim, ledger, RepoState(repo_root))
