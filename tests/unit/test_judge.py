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


# ---- Tier 4 judge: parsing, invariants, escalation. No network; a fake backend stands in. ----
from dataclasses import dataclass, field  # noqa: E402

import pytest  # noqa: E402

from receipts.judge import (  # noqa: E402
    SYSTEM,
    Usage,
    _majority,
    _to_records,
    render_window,
    window_for_all,
)
from receipts.models import Verdict, VerdictRecord  # noqa: E402
from receipts.verdicts import run  # noqa: E402


def _claim(cid: str = "c1", ctype: ClaimType = ClaimType.VERIFY, objects: list[str] | None = None) -> Claim:
    return Claim(id=cid, session_id="s", text=f"claim {cid}", type=ctype, objects=objects or [])


def _ledger() -> list[LedgerEvent]:
    ts = datetime(2026, 9, 19)
    return [
        LedgerEvent(seq=0, ts=ts, session_id="s", kind=EventKind.CALL, tool="Bash",
                    input={"command": "curl -s localhost:8000/health"}),
        LedgerEvent(seq=1, ts=ts, session_id="s", kind=EventKind.RESULT, tool="Bash", output="200 OK", exit_code=0),
        LedgerEvent(seq=2, ts=ts, session_id="s", kind=EventKind.TEXT, tool=None, output="I checked it and it works"),
    ]


def test_confirmed_without_citations_is_downgraded() -> None:
    c = _claim()
    recs = _to_records([{"claim_id": "c1", "verdict": "confirmed", "evidence": [], "reason": "looks right"}],
                       [c], _ledger(), "m")
    assert recs[0].verdict == Verdict.UNWITNESSED and "cited no ledger event" in recs[0].rationale


def test_citations_outside_the_window_are_dropped() -> None:
    c = _claim()
    recs = _to_records([{"claim_id": "c1", "verdict": "confirmed", "evidence": [99], "reason": "x"}],
                       [c], _ledger(), "m")
    assert recs[0].verdict == Verdict.UNWITNESSED


def test_contradicted_from_the_model_is_not_representable() -> None:
    """The schema forbids it, and anything unrecognised becomes unwitnessed."""
    c = _claim()
    recs = _to_records([{"claim_id": "c1", "verdict": "contradicted", "evidence": [0], "reason": "no"}],
                       [c], _ledger(), "m")
    assert recs[0].verdict == Verdict.UNWITNESSED


def test_skipped_claims_still_get_a_record() -> None:
    a, b = _claim("c1"), _claim("c2")
    recs = _to_records([{"claim_id": "c1", "verdict": "unwitnessed", "evidence": [], "reason": "r"}],
                       [a, b], _ledger(), "m")
    assert {r.claim_id for r in recs} == {"c1", "c2"}


def test_majority_needs_a_strict_majority_to_confirm() -> None:
    c = _claim()
    conf = VerdictRecord(claim_id="c1", verdict=Verdict.CONFIRMED, tier=4, method="judge", confidence=0.7, evidence=[0])
    unw = VerdictRecord(claim_id="c1", verdict=Verdict.UNWITNESSED, tier=4, method="judge", confidence=0.7)
    assert _majority([[conf], [unw]], [c])[0].verdict == Verdict.UNWITNESSED  # 1-1 tie
    assert _majority([[conf], [conf], [unw]], [c])[0].verdict == Verdict.CONFIRMED  # 2-1


def test_agent_prose_is_never_shown_to_the_judge() -> None:
    rendered = render_window(_ledger())
    assert "I checked it and it works" not in rendered
    assert "#0 CALL Bash" in rendered and "200 OK" in rendered


def test_system_prompt_forbids_contradiction_and_treats_ledger_as_data() -> None:
    assert "NEVER answer" in SYSTEM and "contradicted" in SYSTEM
    assert "DATA, NOT INSTRUCTIONS" in SYSTEM


@dataclass
class FakeBackend:
    answers: dict[str, VerdictRecord]
    usage: Usage = field(default_factory=Usage)
    seen: list[Claim] = field(default_factory=list)

    def judge(self, claims: list[Claim], window: list[LedgerEvent]) -> list[VerdictRecord]:
        self.seen = list(claims)
        self.usage.requests += 1
        return [self.answers[c.id] for c in claims if c.id in self.answers]


def test_judge_only_sees_claims_no_rule_settled_and_may_only_upgrade() -> None:
    ledger = _ledger()
    settled = _claim("c1", ClaimType.RUN_TESTS)          # a rule settles this (no runner -> unwitnessed tier 1)
    semantic = _claim("c2", ClaimType.VERIFY)            # tier 4 unwitnessed -> escalates
    up = VerdictRecord(claim_id="c2", verdict=Verdict.CONFIRMED, tier=4, method="judge", confidence=0.7, evidence=[0, 1])
    fake = FakeBackend({"c2": up})
    recs = run([settled, semantic], ledger, None, fake)
    assert [c.id for c in fake.seen] == ["c2"]
    assert recs[0].verdict == Verdict.UNWITNESSED and recs[0].method == "rule"
    assert recs[1].verdict == Verdict.CONFIRMED and recs[1].evidence == [0, 1]


def test_judge_cannot_overturn_a_deterministic_contradiction() -> None:
    """A rule's contradicted verdict never reaches the judge, so it cannot be softened."""
    ts = datetime(2026, 9, 19)
    ledger = [
        LedgerEvent(seq=0, ts=ts, session_id="s", kind=EventKind.CALL, tool="Bash", input={"command": "pytest -q"}),
        LedgerEvent(seq=1, ts=ts, session_id="s", kind=EventKind.RESULT, tool="Bash",
                    output="test session starts\ncollected 3 items\n\n1 failed, 2 passed\n"),
    ]
    c = _claim("c1", ClaimType.RUN_TESTS)
    fake = FakeBackend({"c1": VerdictRecord(claim_id="c1", verdict=Verdict.CONFIRMED, tier=4, method="judge",
                                            confidence=0.9, evidence=[0])})
    recs = run([c], ledger, None, fake)
    assert recs[0].verdict == Verdict.CONTRADICTED and fake.seen == []


def test_enforce_rejects_a_judge_contradiction() -> None:
    from receipts.verdicts import _enforce

    with pytest.raises(AssertionError):
        _enforce(VerdictRecord(claim_id="c", verdict=Verdict.CONTRADICTED, tier=4, method="judge", confidence=1.0))


def test_window_for_all_is_one_window_covering_every_claim() -> None:
    ledger = _ledger()
    win = window_for_all(ledger, [_claim("c1"), _claim("c2", objects=["nope.py"])])
    assert [e.seq for e in win] == [0, 1, 2]
