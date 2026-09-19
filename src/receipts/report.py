"""Renderers: Rich terminal receipt, markdown for a PR comment, static HTML report card.

One `Reviewed` in, three surfaces out. The terminal receipt is the demo; the markdown is what the
GitHub Action posts; the HTML card is what the usability sessions put in front of a person.

Marks are the product's vocabulary and never change between surfaces:
    ✓ confirmed   ✗ contradicted   ? unwitnessed   ○ unrecorded   ≈ qualified

Owner: Oliver (terminal, HTML), Ananya (PR comment).
"""
from __future__ import annotations

import html
from collections.abc import Sequence

from rich.console import Console
from rich.text import Text

from .models import Claim, LedgerEvent, Verdict, VerdictRecord

MARK: dict[Verdict, tuple[str, str]] = {
    Verdict.CONFIRMED: ("✓", "green"),
    Verdict.CONTRADICTED: ("✗", "red"),
    Verdict.UNWITNESSED: ("?", "yellow"),
    Verdict.UNRECORDED: ("○", "bright_black"),
    Verdict.QUALIFIED: ("≈", "cyan"),
}
_HEX = {Verdict.CONFIRMED: "#1E7B4E", Verdict.CONTRADICTED: "#B42318", Verdict.UNWITNESSED: "#B25E09",
        Verdict.UNRECORDED: "#5B6675", Verdict.QUALIFIED: "#2457C5"}


def tally(verdicts: Sequence[VerdictRecord]) -> str:
    counts: dict[Verdict, int] = {}
    for r in verdicts:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    return " · ".join(f"{n} {MARK[v][0]}" for v, n in counts.items()) or "no claims found"


def _evidence_for(rec: VerdictRecord, ledger: Sequence[LedgerEvent]) -> list[LedgerEvent]:
    want = set(rec.evidence)
    return [e for e in ledger if e.seq in want]


def terminal(claims: Sequence[Claim], verdicts: Sequence[VerdictRecord],
             ledger: Sequence[LedgerEvent], console: Console, *, show_evidence: bool = False) -> None:
    """The receipt as it appears under the agent's message. The demo surface."""
    by = {c.id: c for c in claims}
    for rec in verdicts:
        claim = by.get(rec.claim_id)
        if claim is None:
            continue
        mark, colour = MARK[rec.verdict]
        line = Text()
        line.append(f"  {mark} ", style=f"bold {colour}")
        line.append(f"{rec.verdict.value:<13}", style=colour)
        line.append(claim.text.strip()[:96])
        console.print(line)
        cited = " ".join(f"#{s}" for s in rec.evidence) or "—"
        meta = f"      tier {rec.tier} · {rec.method} · {cited} · {rec.rationale}"
        if rec.qualifier:
            meta += f" · {rec.qualifier}"
        console.print(Text(meta[:200], style="dim"))
        if show_evidence:
            for e in _evidence_for(rec, ledger):
                body = (e.output or str((e.input or {}).get("command", "")))[:110].replace("\n", " ⏎ ")
                console.print(Text(f"        #{e.seq} {e.tool or ''} {body}", style="dim cyan"))
    console.print(Text(f"  receipts · {tally(verdicts)}", style="bold"))


def markdown(claims: Sequence[Claim], verdicts: Sequence[VerdictRecord], *, source: str = "") -> str:
    """What the GitHub Action posts on a PR."""
    by = {c.id: c for c in claims}
    out = [f"**Receipt for this PR's description** · {len(verdicts)} claims · {tally(verdicts)}"]
    if source:
        out.append(f"<sub>source: {source}</sub>")
    out.append("")
    out.append("| | claim | evidence |")
    out.append("|---|---|---|")
    for rec in verdicts:
        claim = by.get(rec.claim_id)
        if claim is None:
            continue
        cited = ", ".join(f"`#{s}`" for s in rec.evidence) or "—"
        text = claim.text.strip().replace("|", "\\|")[:160]
        out.append(f"| {MARK[rec.verdict][0]} **{rec.verdict.value}** | {text} | {cited} "
                   f"<br><sub>{rec.rationale[:140]}</sub> |")
    blocked = [r for r in verdicts if r.verdict == Verdict.CONTRADICTED]
    if blocked:
        out += ["", f"⚠️ **{len(blocked)} contradicted claim(s).** The description asserts work the "
                    "session log does not support. Label applied: `needs-receipt`."]
    return "\n".join(out)


def html_card(claims: Sequence[Claim], verdicts: Sequence[VerdictRecord],
              ledger: Sequence[LedgerEvent], *, report: str = "", title: str = "Receipt") -> str:
    """A self-contained page: the agent's report with marks, each opening to its evidence."""
    by = {c.id: c for c in claims}
    rows = []
    for rec in verdicts:
        claim = by.get(rec.claim_id)
        if claim is None:
            continue
        mark, _ = MARK[rec.verdict]
        colour = _HEX[rec.verdict]
        ev = "".join(
            f"<div class='ev'><span class='seq'>#{e.seq}</span> <span class='tool'>{html.escape(e.tool or '')}</span> "
            f"{html.escape((e.output or str((e.input or {}).get('command', '')))[:300])}</div>"
            for e in _evidence_for(rec, ledger))
        rows.append(
            f"<details><summary><span class='mark' style='color:{colour}'>{mark}</span>"
            f"<span class='v' style='color:{colour}'>{rec.verdict.value}</span>"
            f"<span class='claim'>{html.escape(claim.text.strip()[:200])}</span></summary>"
            f"<div class='why'>tier {rec.tier} · {rec.method} · {html.escape(rec.rationale[:300])}</div>"
            f"{ev or '<div class=\"ev dim\">no ledger evidence</div>'}</details>")
    return f"""<!doctype html><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
:root{{color-scheme:light dark}}
body{{font:15px/1.55 ui-sans-serif,system-ui,-apple-system,sans-serif;max-width:860px;margin:2rem auto;
padding:0 1rem;background:Canvas;color:CanvasText}}
h1{{font-size:1.3rem;margin:0 0 .2rem}} .sub{{color:#6b7280;font-size:13px;margin-bottom:1.2rem}}
.report{{border-left:3px solid #d1d5db;padding:.6rem 1rem;margin:1rem 0;white-space:pre-wrap;
font-size:14px;color:#4b5563}}
details{{border:1px solid #d9dee6;border-radius:6px;margin:.45rem 0;padding:.5rem .7rem}}
summary{{cursor:pointer;display:flex;gap:.6rem;align-items:baseline;list-style:none}}
summary::-webkit-details-marker{{display:none}}
.mark{{font-weight:700;font-family:ui-monospace,monospace}}
.v{{font-size:12px;text-transform:uppercase;letter-spacing:.06em;min-width:6.5rem}}
.claim{{flex:1}}
.why{{color:#6b7280;font-size:13px;margin:.5rem 0 .4rem}}
.ev{{font-family:ui-monospace,SFMono-Regular,monospace;font-size:12px;background:#f3f4f6;
border-radius:4px;padding:.35rem .5rem;margin:.25rem 0;white-space:pre-wrap;color:#111827}}
.ev.dim{{color:#9ca3af;background:none}} .seq{{color:#6b7280}} .tool{{color:#b45309}}
@media(prefers-color-scheme:dark){{.ev{{background:#1f2937;color:#e5e7eb}} .report{{color:#9ca3af}}}}
</style>
<h1>{html.escape(title)}</h1>
<div class="sub">{len(verdicts)} claims · {tally(verdicts)} · evidence from {len(ledger)} logged events</div>
{f'<div class="report">{html.escape(report[:2000])}</div>' if report else ''}
{''.join(rows)}
"""
