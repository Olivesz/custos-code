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


def window(ledger: list[LedgerEvent], claim: Claim, n: int = 40) -> list[LedgerEvent]:
    """Last n events plus any event touching the claim's paths. NEEDS-DECISION(anush): E1."""
    raise NotImplementedError
