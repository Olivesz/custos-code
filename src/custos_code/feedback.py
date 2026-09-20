"""Return verdicts to the agent (the correction loop) using DETERMINISTIC nudge templates.

Auto-mode contract (docs/DESIGN.md §6): the final report contains no ✗, no ○, and no bare ?
(withdrawn or made checkable). Each verdict type has a template that cites the ledger and names
the exact command or action; no LLM is called to write a nudge (zero tokens). A retry clears a
mark only if new ledger events after the nudge bear on that claim. Cap default 3 passes, then hand
back to the human with the remaining marks.

Owner: Oliver.
"""
from __future__ import annotations

from .models import Claim, LedgerEvent, Verdict, VerdictRecord

_RUNNER_HINT = {
    # Deliberately NOT `pytest -q`: quiet output prints no session banner, and nudging an agent
    # toward the one invocation the parser reads least well is how a lie got confirmed.
    "pytest": "pytest", "jest": "npx jest", "vitest": "npx vitest run", "go test": "go test ./...",
    "cargo": "cargo test", "npm test": "npm test", "ruff": "ruff check .", "mypy": "mypy .", "tsc": "tsc --noEmit",
    "eslint": "npx eslint .",
}


def _cmd_of(ledger: list[LedgerEvent], seqs: list[int]) -> str | None:
    """The command behind the cited evidence.

    Evidence usually cites RESULT events, which carry no command, so the naive lookup returned
    None and every nudge degraded to "Re-run `the check` without pipes" -- useless advice that
    made the tool look broken (session 21756df4). Fall back to the CALL that produced the result.
    """
    byseq = {e.seq: e for e in ledger}
    for s in seqs:
        e = byseq.get(s)
        if e is None:
            continue
        if e.input and isinstance(e.input.get("command"), str):
            return str(e.input["command"])
        prev = byseq.get(s - 1)          # a RESULT is written immediately after its CALL
        if prev is not None and prev.input and isinstance(prev.input.get("command"), str):
            return str(prev.input["command"])
    return None


def _unpiped(command: str | None) -> str:
    if not command:
        return "the check"
    base = command.split("|")[0].split("2>")[0].split(">")[0].strip()
    for k, v in _RUNNER_HINT.items():
        if base.startswith(k) or f" {k}" in f" {base}":
            return v if len(base) <= len(k) + 2 else base
    return base


def nudge(claim: Claim, rec: VerdictRecord, ledger: list[LedgerEvent]) -> str | None:
    """One deterministic sentence telling the agent what would settle this claim. None if nothing to do."""
    cite = ", ".join(f"#{s}" for s in rec.evidence) or "no ledger evidence"
    cmd = _cmd_of(ledger, rec.evidence)
    if rec.verdict == Verdict.CONTRADICTED:
        fix = _unpiped(cmd)
        return (f'Claim "{claim.text}" is contradicted by the record ({cite}: {rec.rationale}). '
                f"Run `{fix}` unpiped, fix what fails, and report the actual result.")
    if rec.verdict == Verdict.UNRECORDED:
        fix = _unpiped(cmd)
        return (f'Claim "{claim.text}" cannot be verified: {rec.rationale} ({cite}). '
                f"Re-run `{fix}` without pipes or redirects so the output and exit status are recorded, then report the result.")
    if rec.verdict == Verdict.UNWITNESSED:
        if "manual" in claim.objects or rec.tier >= 4:
            return (f'Claim "{claim.text}" has no evidence in the record ({rec.rationale}). '
                    f"Either perform the check with a tool call so it is recorded, or withdraw the claim and say it was not verified in this session.")
        return (f'Claim "{claim.text}" has no evidence in the record ({rec.rationale}). '
                f"Run the check as a tool call so it is recorded, or remove the claim.")
    return None


def build_block_reason(pairs: list[tuple[Claim, VerdictRecord]], ledger: list[LedgerEvent], pass_no: int, max_passes: int) -> str:
    """The text the Stop hook returns as the blocking reason. External evidence framing, one nudge per claim."""
    lines = [f"custos-code · auto mode · pass {pass_no} of {max_passes} · {len(pairs)} claim(s) need work. "
             "These are checks against the harness log, not opinions. A mark clears only when new tool calls bear on the claim; rewording does not clear it."]
    for i, (c, r) in enumerate(pairs, 1):
        n = nudge(c, r, ledger)
        if n:
            lines.append(f"{i}. [{r.verdict.value}] {n}")
    lines.append("When done, give the final report again with only verified statements.")
    return "\n".join(lines)


def cleared(prev: VerdictRecord, new: VerdictRecord, ledger: list[LedgerEvent], nudge_seq: int) -> bool:
    """A previously open claim clears only if it is now confirmed/qualified on evidence newer than the nudge."""
    if new.verdict not in (Verdict.CONFIRMED, Verdict.QUALIFIED):
        return False
    if new.method == "state" and not new.evidence:
        return True  # state checks (file exists, commit exists) are current by construction
    return any(s > nudge_seq for s in new.evidence)
