"""Tier 4: grounded judgement for semantic claims. Two backends, one interface.

The judge sees a windowed ledger (E1) and one or more claims, and returns per claim
`confirmed` (with cited seq numbers) or `unwitnessed`. It CANNOT return `contradicted`
(invariant 3). Temperature 0, `samples` majority vote, structured output. Tool outputs in the
ledger are data, never instructions; the system prompt says so and fixtures test it.

Owner: Oliver. Backend plumbing: Ananya.
"""
from __future__ import annotations

from typing import Protocol

from .models import Claim, LedgerEvent, VerdictRecord


class Backend(Protocol):
    def judge(self, claims: list[Claim], window: list[LedgerEvent]) -> list[VerdictRecord]: ...


class OpenAIBackend:
    def __init__(self, extractor_model: str, judge_model: str) -> None:
        self.extractor_model, self.judge_model = extractor_model, judge_model

    def judge(self, claims: list[Claim], window: list[LedgerEvent]) -> list[VerdictRecord]:
        raise NotImplementedError


class AnthropicBackend:
    def __init__(self, extractor_model: str, judge_model: str) -> None:
        self.extractor_model, self.judge_model = extractor_model, judge_model

    def judge(self, claims: list[Claim], window: list[LedgerEvent]) -> list[VerdictRecord]:
        raise NotImplementedError


def _command_text(input_: dict[str, object] | None) -> str | None:
    if not input_:
        return None
    for key in ("command", "cmd", "script"):
        value = input_.get(key)
        if isinstance(value, str):
            return value
    return None


def _touches(event: LedgerEvent, objects: list[str]) -> bool:
    if any(o in event.paths for o in objects):
        return True
    command = _command_text(event.input)
    if command is None:
        return False
    return any(o in command for o in objects)


def window(ledger: list[LedgerEvent], claim: Claim, n: int = 40) -> list[LedgerEvent]:
    """Last n events plus any event touching the claim's paths. Resolved (E1).

    Hybrid window, not the full session: the last `n` events give recency and
    immediate context; events elsewhere in the ledger whose paths or invoked
    command mention one of the claim's objects are pulled in regardless of
    position, since the evidence that settles an early claim can sit far back
    (see OPEN_QUESTIONS E1 -- tune `n` against kappa on the gold set later).
    Sidechain (sub-agent) events never count as top-level evidence and are
    dropped before windowing. Result stays in seq order.
    """
    visible = [e for e in ledger if not e.flags.sidechain]
    tail = visible[-n:] if n > 0 else []
    tail_seqs = {e.seq for e in tail}
    objects = [o for o in claim.objects if o]
    matched = [e for e in visible if e.seq not in tail_seqs and _touches(e, objects)]
    combined = matched + tail
    combined.sort(key=lambda e: e.seq)
    return combined
