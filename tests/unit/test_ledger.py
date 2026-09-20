from datetime import datetime

from custos_code.ledger import chain, verify_chain
from custos_code.models import EventKind, LedgerEvent


def test_chain_roundtrip() -> None:
    evs = [
        LedgerEvent(seq=i, ts=datetime(2026, 9, 19), session_id="s", kind=EventKind.CALL, tool="Bash")
        for i in range(3)
    ]
    chained = chain(evs)
    assert verify_chain(chained)
    chained[1].output = "tampered"
    assert not verify_chain(chained)
