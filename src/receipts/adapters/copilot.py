"""GitHub Copilot coding agent session logs, linked from commits. Confirm export format (A3).

Owner: Ananya.
"""
from __future__ import annotations

from ..models import LedgerEvent, Session


def parse(path: str) -> tuple[Session, list[LedgerEvent], str | None]:
    raise NotImplementedError
