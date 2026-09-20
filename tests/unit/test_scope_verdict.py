"""Scope findings reaching the receipt. docs/SCOPE.md §4, issue #64.

`hooks.py`'s PreToolUse gate decides ask/deny live and never itself writes a CALL event for a
denied action -- a call that never ran leaves nothing to report on, by design. `scope.scan` is the
other half: it re-classifies whatever tool calls a session's ledger already recorded (because
scope was off, in warn mode, or the user approved an "ask") and turns the ones that gate into a
receipt row.
"""
from __future__ import annotations

from datetime import datetime

from custos_code.models import ClaimType, EventFlags, EventKind, LedgerEvent, Verdict
from custos_code.scope import Band, Grant, scan, to_verdict

TS = datetime(2026, 9, 20)


def _call(seq: int, tool: str, inp: dict, sidechain: bool = False) -> LedgerEvent:
    return LedgerEvent(seq=seq, ts=TS, session_id="s", kind=EventKind.CALL, tool=tool,
                       input=inp, flags=EventFlags(sidechain=sidechain))


def test_scan_finds_a_gating_call_in_an_already_recorded_ledger(tmp_path) -> None:
    ledger = [
        _call(0, "Bash", {"command": "pytest -q"}),
        _call(1, "Bash", {"command": "git push --force origin main"}),
    ]
    grant = Grant.for_session(str(tmp_path))
    hits = scan(ledger, grant)
    assert len(hits) == 1
    event, finding = hits[0]
    assert event.seq == 1 and finding.band is Band.RED and finding.rule == "git-force-push"


def test_scan_ignores_sidechain_calls(tmp_path) -> None:
    """A sub-agent's actions are not the top-level agent's blast radius."""
    ledger = [_call(0, "Bash", {"command": "git push --force origin main"}, sidechain=True)]
    grant = Grant.for_session(str(tmp_path))
    assert scan(ledger, grant) == []


def test_scan_finds_nothing_in_an_all_green_session(tmp_path) -> None:
    ledger = [_call(0, "Bash", {"command": "pytest -q"}), _call(1, "Read", {"file_path": "x.py"})]
    grant = Grant.for_session(str(tmp_path))
    assert scan(ledger, grant) == []


def test_to_verdict_is_out_of_scope_never_contradicted(tmp_path) -> None:
    """SCOPE.md §4: a scope violation is a boundary violation, not positive evidence a claim is false."""
    ledger = [_call(3, "Bash", {"command": "git push --force origin main"})]
    grant = Grant.for_session(str(tmp_path))
    (event, finding) = scan(ledger, grant)[0]
    claim, record = to_verdict(event, finding)

    assert record.verdict is Verdict.OUT_OF_SCOPE
    assert record.verdict is not Verdict.CONTRADICTED
    assert record.band == "red"
    assert record.evidence == [3]
    assert claim.source == "scope"
    assert claim.type is ClaimType.OTHER
    assert claim.session_id == "s"


def test_to_verdict_carries_the_yellow_band(tmp_path) -> None:
    ledger = [_call(0, "Bash", {"command": "pip install requests"})]
    grant = Grant.for_session(str(tmp_path))
    (event, finding) = scan(ledger, grant)[0]
    _, record = to_verdict(event, finding)
    assert record.band == "yellow"
    assert record.verdict is Verdict.OUT_OF_SCOPE
