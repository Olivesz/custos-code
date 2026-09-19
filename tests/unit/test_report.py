"""Renderers: same verdicts in, three surfaces out, marks never drift between them."""
from datetime import datetime

from receipts.models import Claim, ClaimType, EventKind, LedgerEvent, Verdict, VerdictRecord
from receipts.report import MARK, html_card, markdown, tally

TS = datetime(2026, 9, 19)


def _fixture() -> tuple[list[Claim], list[VerdictRecord], list[LedgerEvent]]:
    ledger = [
        LedgerEvent(seq=0, ts=TS, session_id="s", kind=EventKind.CALL, tool="Bash",
                    input={"command": "pytest -q | tail -5"}),
        LedgerEvent(seq=1, ts=TS, session_id="s", kind=EventKind.RESULT, tool="Bash",
                    output="collected 0 items"),
    ]
    claims = [Claim(id="c1", session_id="s", text="all 7 tests pass", type=ClaimType.RUN_TESTS),
              Claim(id="c2", session_id="s", text="checked it in the browser", type=ClaimType.VERIFY)]
    recs = [VerdictRecord(claim_id="c1", verdict=Verdict.CONTRADICTED, tier=2, method="rule",
                          confidence=0.9, evidence=[0, 1], rationale="collected 0 items"),
            VerdictRecord(claim_id="c2", verdict=Verdict.UNWITNESSED, tier=4, method="judge",
                          confidence=0.8, evidence=[], rationale="a manual check leaves no trace")]
    return claims, recs, ledger


def test_every_verdict_has_a_mark() -> None:
    assert set(MARK) == set(Verdict)


def test_tally_counts_each_verdict() -> None:
    _c, recs, _l = _fixture()
    t = tally(recs)
    assert "1 ✗" in t and "1 ?" in t


def test_markdown_carries_claim_evidence_and_the_warning() -> None:
    claims, recs, _l = _fixture()
    md = markdown(claims, recs, source="claude_code session abcd1234")
    assert "all 7 tests pass" in md and "`#0`, `#1`" in md
    assert "needs-receipt" in md               # a contradiction must surface the label
    assert "abcd1234" in md
    assert md.count("|") > 8                   # rendered as a table, not prose


def test_markdown_omits_the_warning_when_nothing_is_contradicted() -> None:
    claims, recs, _l = _fixture()
    recs[0].verdict = Verdict.CONFIRMED
    assert "needs-receipt" not in markdown(claims, recs)


def test_html_card_is_self_contained_and_shows_evidence() -> None:
    claims, recs, ledger = _fixture()
    card = html_card(claims, recs, ledger, report="all 7 tests pass", title="T")
    assert card.startswith("<!doctype html>")
    assert "<script" not in card               # no JS: it must open from disk with nothing fetched
    assert "http://" not in card and "https://" not in card   # no external assets
    assert "collected 0 items" in card         # the cited evidence is inlined
    assert "&lt;" not in claims[0].text        # sanity: fixture has no markup to escape


def test_html_card_escapes_markup_in_a_claim() -> None:
    claims, recs, ledger = _fixture()
    claims[0].text = "<script>alert(1)</script> tests pass"
    card = html_card(claims, recs, ledger)
    assert "<script>alert(1)</script>" not in card
    assert "&lt;script&gt;" in card
