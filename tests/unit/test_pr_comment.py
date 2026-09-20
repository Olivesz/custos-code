"""Product sketch B: one comment, worst news first, tier and method on every row (invariant 7)."""

from datetime import UTC, datetime

from custos_code.models import (
    Claim,
    ClaimType,
    Coverage,
    EventKind,
    LedgerEvent,
    Session,
    Verdict,
    VerdictRecord,
)
from custos_code.report import MARKER, pr_comment

TS = datetime(2025, 9, 19, 12, 0, tzinfo=UTC)


def _session() -> Session:
    return Session(
        id="devin-3f9c11aa2b",
        source="devin",
        agent="devin",
        n_events=4,
        ledger_root_hash="abcdef1234567890",
        integrity_score=0.5,
    )


def _ledger() -> list[LedgerEvent]:
    return [
        LedgerEvent(
            seq=0,
            ts=TS,
            session_id="s",
            kind=EventKind.RESULT,
            tool="Bash",
            input={"command": "pytest -q"},
            exit_code=1,
        ),
        LedgerEvent(
            seq=1,
            ts=TS,
            session_id="s",
            kind=EventKind.RESULT,
            tool="Git",
            input={"sha": "9c1b7e2"},
            paths=["/srv/app/upload.py"],
            exit_code=0,
        ),
    ]


def _claims() -> list[Claim]:
    return [
        Claim(
            id="c1",
            session_id="s",
            text="Ran pytest and the suite passed.",
            type=ClaimType.RUN_TESTS,
        ),
        Claim(id="c2", session_id="s", text="Edited upload.py | added retry", type=ClaimType.EDIT),
        Claim(
            id="c3",
            session_id="s",
            text="Checked the staging dashboard by hand.",
            type=ClaimType.VERIFY,
        ),
    ]


def _records() -> list[VerdictRecord]:
    return [
        VerdictRecord(
            claim_id="c1",
            verdict=Verdict.CONTRADICTED,
            tier=2,
            method="rule",
            confidence=0.99,
            evidence=[0],
            rationale="pytest exited 1",
        ),
        VerdictRecord(
            claim_id="c2",
            verdict=Verdict.CONFIRMED,
            tier=1,
            method="state",
            confidence=0.9,
            evidence=[1],
            rationale="commit touches the file",
        ),
        VerdictRecord(
            claim_id="c3",
            verdict=Verdict.UNWITNESSED,
            tier=1,
            method="rule",
            confidence=0.5,
            rationale="no browser evidence in the record",
        ),
    ]


def test_comment_carries_the_marker_and_the_integrity_line() -> None:
    body = pr_comment(_session(), _claims(), _records(), _ledger())
    assert body.startswith(MARKER)
    assert "4 events" in body and "integrity 0.50" in body and "abcdef12" in body
    assert "3 claims" in body


def test_contradictions_come_first_and_every_row_states_tier_and_method() -> None:
    rows = [
        line
        for line in pr_comment(_session(), _claims(), _records(), _ledger()).splitlines()
        if line.startswith("| ")
    ]
    body_rows = [r for r in rows if "---" not in r and "Claim" not in r]
    assert "contradicted" in body_rows[0]
    assert ["unwitnessed" in r for r in body_rows].index(True) < [
        "confirmed" in r for r in body_rows
    ].index(True)
    assert all("·" in r.rsplit("|", 2)[1] for r in body_rows)  # "tier · method"
    assert "2 · rule" in body_rows[0]


def test_evidence_cites_the_ledger_line_its_tool_and_its_exit_code() -> None:
    body = pr_comment(_session(), _claims(), _records(), _ledger())
    assert "log 0 `Bash` pytest -q → exit 1" in body
    assert "log 1 `Git` 9c1b7e2 → exit 0" in body


def test_table_cells_escape_pipes_and_stay_on_one_line() -> None:
    claims = _claims()
    claims[1].text = "Edited upload.py | added retry\nand a test"
    body = pr_comment(_session(), claims, _records(), _ledger())
    row = next(line for line in body.splitlines() if "added retry" in line)
    assert row.count("|") - row.count("\\|") == 5 and "\\|" in row
    assert "added retry and a test" in row


def test_claim_text_cannot_inject_html_into_the_table() -> None:
    # the claim is agent-written text; GFM passes HTML through, so a closing tag would end the
    # table and hide the rows below it -- the contradicted ones
    claims = _claims()
    claims[1].text = "Edited upload.py</table><img src=x onerror=alert(1)>"
    body = pr_comment(_session(), claims, _records(), _ledger())
    assert "</table>" not in body and "<img" not in body
    assert "&lt;/table&gt;" in body


def test_unwitnessed_is_explained_whenever_something_is_contradicted() -> None:
    body = pr_comment(_session(), _claims(), _records(), _ledger())
    assert "Unwitnessed is not an accusation" in body
    clean = [r for r in _records() if r.verdict is not Verdict.CONTRADICTED]
    assert "Unwitnessed is not an accusation" not in pr_comment(
        _session(), _claims(), clean, _ledger()
    )


def test_coverage_line_names_unrequested_work() -> None:
    coverage = [
        Coverage(requirement="retry on 502", claim_ids=["c2"], status="done"),
        Coverage(requirement="reformatted the logger", status="unrequested"),
    ]
    body = pr_comment(_session(), _claims(), _records(), _ledger(), coverage=coverage)
    assert "Intent coverage:** 1 of 1" in body and "1 unrequested change" in body


def test_no_claims_still_produces_one_honest_comment() -> None:
    body = pr_comment(_session(), [], [], _ledger())
    assert body.startswith(MARKER) and "nothing to check" in body
    assert "|" not in body
