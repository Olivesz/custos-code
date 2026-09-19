"""Return verdicts to the agent (the correction loop) using DETERMINISTIC nudge templates.

Auto-mode contract: the final report contains no ✗, no ○, and no bare ? (withdrawn or made
checkable). Each verdict type has a template that cites the ledger and names the exact
command or action; no LLM is called to write a nudge (zero tokens; Token Company story).
A retry clears a mark only if new ledger events after the nudge bear on that claim.
Cap default 3 passes, then hand back to the human with the remaining marks.

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
