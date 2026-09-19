"""Return contradictions to the agent (the correction loop).

Claude Code Stop hook: exit 2 with the evidence on stderr, or JSON {"decision": "block", "reason": ...}.
Only `contradicted` blocks by default (P1). The message is framed as external tool evidence and
cites ledger seq numbers. Channel variants (tool result / user message / system) are a bench
experiment (P3); the correction rate per channel is a slide.

Owner: Oliver.
"""
from __future__ import annotations

from .models import VerdictRecord


def format_block_reason(verdicts: list[VerdictRecord]) -> str:
    raise NotImplementedError
