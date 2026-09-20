"""One place that decides whether to stop an agent before it acts.

Scope and architecture were two gates. They asked different questions -- "how far does this write
reach" and "does the documented architecture connect these components" -- but they answered the
same one: should this tool call happen. Two gates meant two mode checks, two fail-open paths, two
copies of the attended/unattended rule, and two places to look when the answer was wrong.

The split that survives is detector / policy, not scope / architecture:

    detectors   `scope.classify`, `arch.crossings`   say only what they observe
    watchdog    this module                          decides what to do about it

A detector never decides and never reads another detector's output. That independence is the
point: two checks that consult each other are one check with extra latency.

Three rules the whole file exists to enforce.

**Fail open, always.** A checker that cannot run is not evidence about the agent. Every error path
here ends in "allow", and a detector that raises is dropped rather than promoted to a refusal.

**Only a measured detector may deny.** `scope` bands blast radius, which is a fact about the
filesystem, so RED there can refuse. `arch` reads a diagram written by someone who is not in this
session and may be out of date -- its strongest honest response is to ask a human. Encoded in
`Check.may_deny`, not in prose, because that is the kind of rule that rots.

**Worst wins, but the reason is specific.** A call that trips two detectors is reported by the one
that matters most, with its own words. "Blocked by policy" teaches nobody anything.

Owner: Oliver.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from . import arch as arch_mod
from . import scope as scope_mod

Decision = Literal["allow", "ask", "deny"]
_RANK: dict[Decision, int] = {"allow": 0, "ask": 1, "deny": 2}

WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")


@dataclass(frozen=True)
class Observation:
    """What one detector saw. It carries no decision -- that is the watchdog's job."""
    detector: str
    severity: Decision       # the strongest response this observation could justify
    rule: str
    detail: str
    may_deny: bool = True    # False for detectors whose evidence cannot support a refusal


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    reason: str
    observations: tuple[Observation, ...] = ()


def _scope_observation(payload: dict[str, Any], grant: scope_mod.Grant,
                       policy: scope_mod.Policy) -> Observation | None:
    """Blast radius: where the write lands, and whether anything can undo it.

    A fact about the filesystem, identical in every repo, so this one is allowed to deny.
    """
    raw = payload.get("tool_input")
    inp: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    f = scope_mod.classify(str(payload.get("tool_name", "")), inp, grant, policy=policy)
    if not f.gates:
        return None
    sev: Decision = "deny" if f.band is scope_mod.Band.RED else "ask"
    return Observation(detector="scope", severity=sev, rule=f.rule, detail=f.detail)


def _arch_observation(payload: dict[str, Any], written: list[str]) -> Observation | None:
    """Documented boundaries: does the repo's own diagram connect what is being changed together.

    `may_deny=False`. The evidence is a hand-maintained diagram, so a crossing is a question for a
    human, never grounds to refuse. A stale diagram must not become the thing that blocks correct
    work.
    """
    if str(payload.get("tool_name", "")) not in WRITE_TOOLS:
        return None
    raw = payload.get("tool_input")
    inp: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    target, cwd = inp.get("file_path"), payload.get("cwd")
    if not isinstance(target, str) or not isinstance(cwd, str) or not cwd:
        return None
    architecture = arch_mod.load(cwd)
    if not architecture:
        return None
    rel = os.path.relpath(target, cwd).replace(os.sep, "/")
    if rel.startswith(".."):
        return None  # outside the repo is the scope detector's question, not this one
    new = [c for c in arch_mod.crossings(architecture, [*written, rel])
           if rel in c.paths_a or rel in c.paths_b]
    if not new:
        return None
    c = new[0]
    return Observation(
        detector="architecture", severity="ask", may_deny=False, rule="undeclared-boundary",
        detail=(f"{c.a_label} and {c.b_label} are both being changed, and "
                f"{', '.join(architecture.sources)} declares no edge between them."))


def observe(payload: dict[str, Any], grant: scope_mod.Grant, policy: scope_mod.Policy,
            written: list[str]) -> list[Observation]:
    """Run every detector. One that raises is dropped, never promoted to a refusal."""
    detectors: list[tuple[str, Callable[[], Observation | None]]] = [
        ("scope", lambda: _scope_observation(payload, grant, policy)),
        ("architecture", lambda: _arch_observation(payload, written)),
    ]
    out: list[Observation] = []
    for name, fn in detectors:
        try:
            got = fn()
        except Exception as e:  # noqa: BLE001 - a detector that breaks must not stop the agent
            print(f"custos-code: {name} check failed ({type(e).__name__}); allowing.",
                  file=sys.stderr)
            continue
        if got is not None:
            out.append(got)
    return out


def decide(observations: list[Observation], *, unattended: bool) -> Verdict:
    """Worst wins, and the winner explains itself in its own words.

    `unattended` escalates an ask to a deny, because "ask" with nobody reading it is a hang. That
    only applies to detectors permitted to deny in the first place: an unattended run does not make
    a stale diagram more authoritative.
    """
    if not observations:
        return Verdict("allow", "")
    best: Observation | None = None
    best_rank = -1
    for ob in observations:
        sev: Decision = ob.severity
        if unattended and sev == "ask":
            sev = "deny"
        # The cap is the single rule. Guarding the escalation above as well would be a second
        # branch with identical behaviour, which mutation testing showed nothing can distinguish
        # -- and an untested branch is how a policy rule rots without anyone noticing.
        if sev == "deny" and not ob.may_deny:
            sev = "ask"
        if _RANK[sev] > best_rank:
            best, best_rank = ob, _RANK[sev]
    assert best is not None
    decision: Decision = ("allow", "ask", "deny")[best_rank]
    if decision == "allow":
        return Verdict("allow", "", tuple(observations))
    verb = "blocked" if decision == "deny" else "needs your approval"
    return Verdict(decision, f"custos-code · {verb} · {best.rule}: {best.detail}",
                   tuple(observations))
