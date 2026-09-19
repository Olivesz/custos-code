"""Append-only, hash-chained event log per session (Tier 0).

Owns: constructing LedgerEvent rows from adapter output, redaction, truncation flags,
the hash chain, SQLite persistence, JSONL export/import.
Must never: accept events from model text; confirm anything; store an unredacted secret.

Owner: Oliver. Hash chain and integrity checks: Anush.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from .models import LedgerEvent


def canonical(event: LedgerEvent) -> str:
    d = event.model_dump(mode="json")
    d.pop("hash", None)
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def chain(events: Iterable[LedgerEvent]) -> list[LedgerEvent]:
    """Assign prev_hash/hash in sequence. Idempotent on already-chained input."""
    out: list[LedgerEvent] = []
    prev = ""
    for e in events:
        e.prev_hash = prev
        e.hash = hashlib.sha256((prev + canonical(e)).encode()).hexdigest()
        prev = e.hash
        out.append(e)
    return out


def verify_chain(events: list[LedgerEvent]) -> bool:
    prev = ""
    for e in events:
        if e.prev_hash != prev:
            return False
        if hashlib.sha256((prev + canonical(e)).encode()).hexdigest() != e.hash:
            return False
        prev = e.hash
    return True


def redact(text: str) -> str:
    """Replace secret-shaped substrings before hashing. NEEDS-DECISION(oliver): gitleaks vs detect-secrets rules."""
    raise NotImplementedError


class LedgerStore:
    """SQLite per session at ~/.receipts/sessions/<id>.sqlite. NEEDS-DECISION(anush): DuckDB for cross-session."""

    def __init__(self, path: str) -> None:
        self.path = path

    def append(self, events: list[LedgerEvent]) -> None:
        raise NotImplementedError

    def load(self) -> list[LedgerEvent]:
        raise NotImplementedError
