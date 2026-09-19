"""OpenTelemetry GenAI spans. Output messages and tool results are opt-in in the convention; mark missing ones unrecorded.

Owner: Ananya.
"""
from __future__ import annotations

from ..models import LedgerEvent, Session


def parse(path: str) -> tuple[Session, list[LedgerEvent], str | None]:
    raise NotImplementedError
