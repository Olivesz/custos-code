"""Devin: public API exposes session metadata, chat messages, structured_output, PR list only. Ledger = PR description + structured_output as the report; CI log + git + filesystem as evidence (A2).

Owner: Ananya.
"""
from __future__ import annotations

from ..models import LedgerEvent, Session


def parse(path: str) -> tuple[Session, list[LedgerEvent], str | None]:
    raise NotImplementedError
