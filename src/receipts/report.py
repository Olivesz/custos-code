"""Renderers: Rich terminal table, PR comment markdown, static HTML report card.

Same VerdictRecord list in, four surfaces out. The terminal receipt is the demo; the PR comment
is the GitHub Action (Ananya); the report card is for the usability sessions.

The PR comment is product sketch B in docs/DESIGN.md §6: one comment per PR, verdict table with
evidence citations, intent coverage line, and a marker comment so the Action updates in place
instead of stacking. Invariant 7 holds here too -- every row prints its tier and method -- and no
verdict is ever upgraded for presentation.

Owner: Oliver (terminal), Ananya (PR comment).
"""

from __future__ import annotations

from .models import Claim, Coverage, LedgerEvent, Session, Verdict, VerdictRecord

MARKER = "<!-- receipts-bot: pr-receipt -->"

MARK = {
    Verdict.CONFIRMED: "✅ confirmed",
    Verdict.CONTRADICTED: "❌ contradicted",
    Verdict.UNWITNESSED: "❓ unwitnessed",
    Verdict.UNRECORDED: "⭕ unrecorded",
    Verdict.QUALIFIED: "➖ qualified",
}
ORDER = [
    Verdict.CONTRADICTED,
    Verdict.QUALIFIED,
    Verdict.UNRECORDED,
    Verdict.UNWITNESSED,
    Verdict.CONFIRMED,
]


def _cell(text: str, limit: int = 160) -> str:
    """Markdown table cells cannot hold newlines or bare pipes."""
    flat = " ".join(text.split())
    if len(flat) > limit:
        flat = flat[: limit - 1].rstrip() + "…"
    return flat.replace("|", "\\|")


def _evidence(record: VerdictRecord, ledger: list[LedgerEvent]) -> str:
    by_seq = {e.seq: e for e in ledger}
    parts: list[str] = []
    for seq in record.evidence[:3]:
        event = by_seq.get(seq)
        if event is None:
            parts.append(f"log {seq}")
            continue
        detail = ""
        if event.input:
            detail = str(
                event.input.get("command")
                or event.input.get("file_path")
                or event.input.get("path")
                or event.input.get("sha")
                or ""
            )
        if not detail and event.paths:
            detail = event.paths[0]
        label = f"log {seq}"
        if event.tool:
            label += f" `{event.tool}`"
        if detail:
            label += f" {_cell(detail, 48)}"
        if event.exit_code is not None:
            label += f" → exit {event.exit_code}"
        parts.append(label)
    return ", ".join(parts) if parts else "—"


def _coverage_line(coverage: list[Coverage]) -> str:
    if not coverage:
        return ""
    done = sum(1 for c in coverage if c.status == "done")
    requested = sum(1 for c in coverage if c.status != "unrequested")
    unrequested = [c.requirement for c in coverage if c.status == "unrequested"]
    needs_human = sum(1 for c in coverage if c.status == "needs_human")
    bits = [f"**Intent coverage:** {done} of {requested} requested items claimed"]
    if unrequested:
        bits.append(
            f"{len(unrequested)} unrequested change{'s' if len(unrequested) > 1 else ''} "
            f"({', '.join(_cell(u, 40) for u in unrequested[:3])})"
        )
    if needs_human:
        bits.append(f"{needs_human} needs human")
    return " · ".join(bits)


def pr_comment(
    session: Session,
    claims: list[Claim],
    records: list[VerdictRecord],
    ledger: list[LedgerEvent],
    *,
    pr_url: str | None = None,
    coverage: list[Coverage] | None = None,
    receipt_url: str | None = None,
) -> str:
    """Render the PR receipt (sketch B). Contradictions first: a reviewer reads the worst news first."""
    by_id = {c.id: c for c in claims}
    counts = {v: sum(1 for r in records if r.verdict is v) for v in Verdict}
    source = f"{session.agent} session `{session.id[:12]}`"
    headline = " · ".join(
        [
            f"**Receipt for this PR's description** · {len(records)} claim{'s' if len(records) != 1 else ''}",
            f"source: {source}",
        ]
    )
    integrity = (
        f"ledger `{session.ledger_root_hash[:8]}` · {session.n_events} events · "
        f"integrity {session.integrity_score:.2f}"
    )

    lines = [MARKER, headline, "", integrity, ""]
    if not records:
        lines.append("_No claims found in the description: nothing to check._")
        return "\n".join(lines) + "\n"

    lines += ["| | Claim | Evidence | Tier |", "|---|---|---|---|"]
    for verdict in ORDER:
        for record in [r for r in records if r.verdict is verdict]:
            claim = by_id.get(record.claim_id)
            text = _cell(claim.text if claim else record.claim_id)
            rationale = _cell(record.rationale, 80)
            evidence = _evidence(record, ledger)
            if record.qualifier:
                rationale = (
                    f"{rationale} ({_cell(record.qualifier, 60)})"
                    if rationale
                    else _cell(record.qualifier, 60)
                )
            detail = f"{evidence}<br>{rationale}" if rationale else evidence
            lines.append(
                f"| {MARK[verdict]} | {text} | {detail} | {record.tier} · {record.method} |"
            )

    summary = " · ".join(f"{counts[v]} {MARK[v].split(' ')[1]}" for v in ORDER if counts[v])
    lines += ["", summary]
    line = _coverage_line(coverage or [])
    if line:
        lines.append("")
        lines.append(line)
    if counts[Verdict.CONTRADICTED]:
        lines += [
            "",
            "> A contradicted claim means the log carries positive evidence against it "
            "(a failing exit code, a command that never ran, a file that is not there). "
            "Unwitnessed is not an accusation: the record simply does not say.",
        ]
    footer = []
    if receipt_url:
        footer.append(f"[Open full receipt]({receipt_url})")
    if pr_url:
        footer.append(f"[PR]({pr_url})")
    footer.append("generated by `receipts pr-comment`")
    lines += ["", f"<sub>{' · '.join(footer)}</sub>"]
    return "\n".join(lines) + "\n"
