"""Report -> atomic claims (and request/plan -> requirements).

Owns: the extraction prompt, the structured-output schema, the regex fallback for mechanical
claim types (run_tests, edit, create, commit). Drops opinions, plans, and questions.
Must never: invent a claim that is not a verbatim span of the report.

Measured on the gold set: extraction recall target >= 0.85 (EVIDENCE_PLAN §2).
NEEDS-DECISION(oliver): E6 — LLM vs regex for mechanical types; decide on gold-set recall.

Owner: Oliver.
"""
from __future__ import annotations

from .models import Claim


def extract(report: str, session_id: str) -> list[Claim]:
    raise NotImplementedError


def extract_regex(report: str, session_id: str) -> list[Claim]:
    """The dumb baseline. Always shipped beside the LLM path (AGENTS.md invariant 8)."""
    raise NotImplementedError
