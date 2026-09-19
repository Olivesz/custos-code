from datetime import datetime

from receipts.judge import window
from receipts.models import Claim, ClaimType, EventFlags, EventKind, LedgerEvent


def _event(
    seq: int,
    *,
    paths: list[str] | None = None,
    input_: dict[str, object] | None = None,
    sidechain: bool = False,
) -> LedgerEvent:
    return LedgerEvent(
        seq=seq,
        ts=datetime(2026, 9, 19),
        session_id="s",
        kind=EventKind.CALL,
        tool="Bash",
        paths=paths or [],
        input=input_,
        flags=EventFlags(sidechain=sidechain),
    )


def test_window_pulls_in_path_matched_event_outside_the_tail() -> None:
    ledger = [_event(0, paths=["auth.py"])] + [_event(i) for i in range(1, 51)]
    claim = Claim(id="c1", session_id="s", text="edited auth.py", type=ClaimType.EDIT, objects=["auth.py"])

    result = window(ledger, claim, n=10)

    seqs = [e.seq for e in result]
    assert 0 in seqs
    assert seqs == sorted(seqs)
    assert seqs[-10:] == list(range(41, 51))


def test_window_drops_sidechain_events_even_when_path_matched() -> None:
    ledger = [_event(0, paths=["auth.py"], sidechain=True)] + [_event(i) for i in range(1, 51)]
    claim = Claim(id="c1", session_id="s", text="edited auth.py", type=ClaimType.EDIT, objects=["auth.py"])

    result = window(ledger, claim, n=10)

    assert 0 not in [e.seq for e in result]


def test_window_matches_on_command_text_not_just_paths() -> None:
    ledger = [_event(0, input_={"command": "pytest tests/test_auth.py"})]
    ledger += [_event(i) for i in range(1, 51)]
    claim = Claim(
        id="c1",
        session_id="s",
        text="ran the auth tests",
        type=ClaimType.RUN_TESTS,
        objects=["tests/test_auth.py"],
    )

    result = window(ledger, claim, n=10)

    assert 0 in [e.seq for e in result]


def test_window_is_last_n_only_when_nothing_matches() -> None:
    ledger = [_event(i) for i in range(50)]
    claim = Claim(id="c1", session_id="s", text="did something unrelated", type=ClaimType.OTHER, objects=["nope.py"])

    result = window(ledger, claim, n=10)

    assert [e.seq for e in result] == list(range(40, 50))
