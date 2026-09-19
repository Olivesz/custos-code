"""Adapters turn a source (harness transcript, hook payload, vendor export) into LedgerEvents.

Contract: every adapter is a function `parse(path_or_payload) -> tuple[Session, list[LedgerEvent], str | None]`
returning the session, the chained events, and the final report text if present.
Adapters must set flags.truncated / flags.piped / flags.sidechain honestly; downstream tiers rely on them.
Golden tests live in tests/golden/<adapter>/ : real input in, expected JSONL out.
"""
