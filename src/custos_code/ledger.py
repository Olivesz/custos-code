"""Append-only, hash-chained event log per session (Tier 0).

Owns: constructing LedgerEvent rows from adapter output, redaction, truncation flags,
the hash chain, SQLite persistence, JSONL export/import.
Must never: accept events from model text; confirm anything; store an unredacted secret.

Owner: Oliver. Hash chain and integrity checks: Anush.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import overload

from .models import LedgerEvent

MAX_OUTPUT_BYTES = 4096

# Secret-shaped substrings replaced before hashing. Conservative on purpose: better to over-redact a
# fixture than to store a token. NEEDS-DECISION(oliver): adopt gitleaks rules for breadth.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai", re.compile(r"sk-[A-Za-z0-9_-]{16,}")),
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}")),
    ("github", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("slack", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("url-cred", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")),
    ("bearer", re.compile(r"(?i)(?<=bearer )[A-Za-z0-9._-]{20,}")),
    ("env-assign", re.compile(r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*)=([^\s'\"]{8,})")),
]


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


@overload
def redact(value: str) -> str: ...
@overload
def redact(value: dict[str, object]) -> dict[str, object]: ...
def redact(value: str | dict[str, object]) -> str | dict[str, object]:
    """Replace secret-shaped substrings with [REDACTED:kind]. Applied before hashing (invariant 9)."""
    if isinstance(value, dict):
        return {k: (redact(v) if isinstance(v, str | dict) else v) for k, v in value.items()}
    out = value
    for kind, pat in _SECRET_PATTERNS:
        if kind == "env-assign":
            out = pat.sub(lambda m: f"{m.group(1)}=[REDACTED:env]", out)
        else:
            out = pat.sub(f"[REDACTED:{kind}]", out)
    return out


class LedgerStore:
    """SQLite per session at ~/.custos-code/sessions/<id>.sqlite. NEEDS-DECISION(anush): DuckDB for cross-session."""

    def __init__(self, path: str) -> None:
        self.path = path

    def append(self, events: list[LedgerEvent]) -> None:
        raise NotImplementedError

    def load(self) -> list[LedgerEvent]:
        raise NotImplementedError
