"""Renderers: Rich terminal receipt, markdown for a PR comment, static HTML report card.

One `Reviewed` in, three surfaces out. The terminal receipt is the demo; the markdown is what the
GitHub Action posts; the HTML card is what the usability sessions put in front of a person.

Marks are the product's vocabulary and never change between surfaces:
    ✓ confirmed   ✗ contradicted   ? unwitnessed   ○ unrecorded   ≈ qualified   ⚠ out_of_scope

Owner: Oliver (terminal, HTML), Ananya (PR comment).
"""

from __future__ import annotations

import html
from collections.abc import Sequence

from rich.console import Console
from rich.text import Text

from .models import Claim, Coverage, LedgerEvent, Session, Verdict, VerdictRecord

MARKER = "<!-- custos-code-bot: pr-receipt -->"
ORDER = [
    Verdict.CONTRADICTED,
    Verdict.OUT_OF_SCOPE,
    Verdict.QUALIFIED,
    Verdict.UNRECORDED,
    Verdict.UNWITNESSED,
    Verdict.CONFIRMED,
]

MARK: dict[Verdict, tuple[str, str]] = {
    Verdict.CONFIRMED: ("✓", "green"),
    Verdict.CONTRADICTED: ("✗", "red"),
    Verdict.UNWITNESSED: ("?", "yellow"),
    Verdict.UNRECORDED: ("○", "bright_black"),
    Verdict.QUALIFIED: ("≈", "cyan"),
    Verdict.OUT_OF_SCOPE: ("⚠", "magenta"),
}
_HEX = {
    Verdict.CONFIRMED: "#1E7B4E",
    Verdict.CONTRADICTED: "#B42318",
    Verdict.UNWITNESSED: "#B25E09",
    Verdict.UNRECORDED: "#5B6675",
    Verdict.QUALIFIED: "#2457C5",
    Verdict.OUT_OF_SCOPE: "#8A2BB2",
}


def tally(verdicts: Sequence[VerdictRecord]) -> str:
    counts: dict[Verdict, int] = {}
    for r in verdicts:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    return " · ".join(f"{n} {MARK[v][0]}" for v, n in counts.items()) or "no claims found"


def _evidence_for(rec: VerdictRecord, ledger: Sequence[LedgerEvent]) -> list[LedgerEvent]:
    want = set(rec.evidence)
    return [e for e in ledger if e.seq in want]


def terminal(
    claims: Sequence[Claim],
    verdicts: Sequence[VerdictRecord],
    ledger: Sequence[LedgerEvent],
    console: Console,
    *,
    show_evidence: bool = False,
) -> None:
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
                body = (e.output or str((e.input or {}).get("command", "")))[:110].replace(
                    "\n", " ⏎ "
                )
                console.print(Text(f"        #{e.seq} {e.tool or ''} {body}", style="dim cyan"))
    console.print(Text(f"  custos-code · {tally(verdicts)}", style="bold"))


def markdown(
    claims: Sequence[Claim], verdicts: Sequence[VerdictRecord], *, source: str = ""
) -> str:
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
        out.append(
            f"| {MARK[rec.verdict][0]} **{rec.verdict.value}** | {text} | {cited} "
            f"<br><sub>{rec.rationale[:140]}</sub> |"
        )
    blocked = [r for r in verdicts if r.verdict == Verdict.CONTRADICTED]
    if blocked:
        out += [
            "",
            f"⚠️ **{len(blocked)} contradicted claim(s).** The description asserts work the "
            "session log does not support. Label applied: `needs-receipt`.",
        ]
    return "\n".join(out)


def html_card(
    claims: Sequence[Claim],
    verdicts: Sequence[VerdictRecord],
    ledger: Sequence[LedgerEvent],
    *,
    report: str = "",
    title: str = "Receipt",
    usage: dict[str, int] | None = None,
    model: str = "",
    chain_root: str = "",
) -> str:
    """A self-contained page with four levels of detail, coarsest first.

    A receipt has to serve two readers at once: someone deciding in three seconds whether to trust
    "done", and someone who needs the exact ledger line behind one sentence. The terminal render
    can only pick one, so it prints everything and the important part scrolls away.

    Here the levels nest. The tally is one line. The report is the agent's own prose with a mark on
    each claim, so an unmarked sentence stays visibly unmarked -- marking everything would train
    people to ignore marks. Claims open to tier, method and rationale. The ledger is what the
    harness recorded. Cost says what the verdicts actually cost, separated by how they were
    settled, because "free" and "billed" is the distinction that decides whether this runs on every
    turn.

    Clicking a claim jumps to the ledger with its cited events highlighted: a verdict without a
    citation does not exist in this product, so the citation has to be one click away.

    Everything rendered is real. `usage` and `chain_root` are optional because the deterministic
    path produces no usage at all -- and a zero there is the honest answer, not missing data.
    """
    by = {c.id: c for c in claims}
    use = usage or {}

    def esc(t: str) -> str:
        return html.escape(t)

    # --- the report, with a mark on each claim and nothing on anything else -----------------
    marked = esc(report)
    for rec in verdicts:
        claim = by.get(rec.claim_id)
        if claim is None or not claim.text.strip():
            continue
        needle = esc(claim.text.strip())
        if needle not in marked:
            continue
        mark, _ = MARK[rec.verdict]
        colour = _HEX[rec.verdict]
        marked = marked.replace(
            needle,
            f"{needle}<a class='mk' style='color:{colour}' href='#claim-{esc(rec.claim_id)}' "
            f"title='{esc(rec.verdict.value)} · tier {rec.tier} · {esc(rec.method)}'>{mark}</a>",
            1,
        )

    # --- claims ------------------------------------------------------------------------------
    crows = []
    for rec in verdicts:
        claim = by.get(rec.claim_id)
        if claim is None:
            continue
        mark, _ = MARK[rec.verdict]
        colour = _HEX[rec.verdict]
        links = " ".join(f"<a href='#seq-{n}'>#{n}</a>" for n in rec.evidence) \
            or "<span class='dim'>no citation</span>"
        crows.append(
            f"<div class='claim' id='claim-{esc(rec.claim_id)}'>"
            f"<span class='m' style='color:{colour}'>{mark}</span>"
            f"<span><span class='t'>{esc(claim.text.strip()[:240])}</span>"
            f"<span class='v' style='color:{colour}'>{rec.verdict.value}</span>"
            f"<span class='why'>{esc(rec.rationale[:320])}</span>"
            f"<span class='cites'>{links}</span></span>"
            f"<span class='tier'>tier {rec.tier} · {esc(rec.method)}</span></div>"
        )

    # --- ledger -------------------------------------------------------------------------------
    lrows = []
    for e in ledger:
        flags = [k for k, v in e.flags.model_dump().items() if v]
        body = e.output or str((e.input or {}).get("command", "")) or ""
        rc = ("" if e.exit_code is None
              else f"<span class='rc{' bad' if e.exit_code else ''}'>exit {e.exit_code}</span>")
        lrows.append(
            f"<div class='led' id='seq-{e.seq}'><span class='s'>#{e.seq}</span>"
            f"<span class='k'>{esc(e.kind.value)}</span>"
            f"<span class='tool'>{esc(e.tool or '')}</span>"
            f"<span class='o'>{esc(' '.join(body.split())[:200])} {rc}"
            + "".join(f"<span class='flag'>⚑ {esc(f)}</span>" for f in flags)
            + "</span></div>"
        )

    # --- cost ---------------------------------------------------------------------------------
    free = sum(1 for r in verdicts if r.tier < 4)
    billed = len(verdicts) - free
    tok_in, tok_cached = use.get("input_tokens", 0), use.get("cached_input_tokens", 0)
    tok_out, reqs = use.get("output_tokens", 0), use.get("requests", 0)
    cost_cards = [
        (str(len(verdicts)), "claims"),
        (str(free), "settled deterministically · $0"),
        (str(billed), "needed a model"),
        (str(reqs), "model request" + ("" if reqs == 1 else "s")),
        (f"{tok_in:,}", f"input tokens ({tok_cached:,} cached)"),
        (f"{tok_out:,}", "output tokens"),
    ]
    ccards = "".join(f"<div class='c'><div class='n'>{n}</div><div class='l'>{esc(lab)}</div></div>"
                     for n, lab in cost_cards)
    # Deliberately no dollar figure: config.example.toml prices the frontier models at 0.00, so
    # any total computed from it would read $0.00 and be a lie of precision. Tokens are observed.
    cnote = ("<p class='note'>Token counts are measured. No dollar total is shown because the "
             "price table ships with placeholder zeros -- a computed $0.00 would be a lie of "
             "precision, not a cheap session.</p>")

    head = (f"{len(verdicts)} claims · {tally(verdicts)} · {len(ledger)} logged events"
            + (f" · {esc(model)}" if model else "")
            + (f" · chain {esc(chain_root[:8])}" if chain_root else ""))

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>
:root{{--paper:#F7F7F5;--ink:#16181D;--muted:#5C6672;--rule:#DCE0E6;--accent:#2457C5;
--panel:#FFFFFF;--code:#F1F3F6;color-scheme:light dark}}
@media(prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--paper:#0F1218;--ink:#E6EAF0;
--muted:#96A0B0;--rule:#28303C;--accent:#7FA6F5;--panel:#151A22;--code:#1B212B}}}}
:root[data-theme="dark"]{{--paper:#0F1218;--ink:#E6EAF0;--muted:#96A0B0;--rule:#28303C;
--accent:#7FA6F5;--panel:#151A22;--code:#1B212B}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);padding:24px 16px 72px;
font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
.wrap{{max-width:1080px;margin:0 auto}}
h1{{font-size:1.35rem;margin:0 0 4px;letter-spacing:-.01em}}
.sub{{color:var(--muted);font-size:13px;margin:0 0 18px;font-variant-numeric:tabular-nums}}
.tabs{{display:flex;gap:4px;border-bottom:1px solid var(--rule);margin-bottom:0;overflow-x:auto}}
.pane{{background:var(--panel);border:1px solid var(--rule);border-top:0;border-radius:0 0 8px 8px;
padding:16px 18px}}
.tabin{{position:absolute;left:-9999px}}
.pane{{display:none}}
#t-report:checked~#p-report,#t-claims:checked~#p-claims,
#t-ledger:checked~#p-ledger,#t-cost:checked~#p-cost{{display:block}}
.tabs label{{font-size:13.5px;padding:8px 14px;color:var(--muted);cursor:pointer;
border-bottom:2px solid transparent;white-space:nowrap}}
#t-report:checked~.tabs label[for="t-report"],#t-claims:checked~.tabs label[for="t-claims"],
#t-ledger:checked~.tabs label[for="t-ledger"],#t-cost:checked~.tabs label[for="t-cost"]{{
color:var(--ink);border-bottom-color:var(--accent);font-weight:500}}
.tabs label:focus-visible{{outline:2px solid var(--accent);outline-offset:-2px}}
/* A cited row highlights when linked to. `:target` is how a claim's citation reaches its
   evidence without a line of JavaScript, so the card still works where scripts are stripped. */
.led:target{{background:color-mix(in srgb,#D29922 26%,transparent);
outline:1px solid #D29922;outline-offset:-1px;scroll-margin-block:40vh}}
.report{{white-space:pre-wrap;font-size:14.5px;line-height:1.7}}
.mk{{font-family:ui-monospace,SFMono-Regular,monospace;font-weight:700;margin-left:3px;
text-decoration:none}}
.mk:hover{{text-decoration:underline}}
.cites a{{color:var(--accent);text-decoration:none;margin-right:6px}}
.cites a:hover{{text-decoration:underline}} .dim{{color:var(--muted)}}
.claim:target{{background:color-mix(in srgb,var(--accent) 12%,transparent);scroll-margin-block:30vh}}
.claim{{display:grid;grid-template-columns:22px 1fr auto;gap:10px;padding:10px 4px;
border-bottom:1px solid var(--rule);cursor:pointer;align-items:start}}
.claim:last-child{{border-bottom:0}}
.claim .m{{font-family:ui-monospace,monospace;font-weight:700;text-align:center}}
.claim .t{{display:block}}
.claim .v{{display:inline-block;font-size:11px;text-transform:uppercase;letter-spacing:.07em;margin-top:3px;font-weight:600}}
.claim .why{{display:block;color:var(--muted);font-size:13px;margin-top:3px}}
.claim .cites{{display:block;color:var(--muted);font-size:12px;margin-top:3px;
font-family:ui-monospace,monospace}}
.claim .tier{{color:var(--muted);font-size:12px;white-space:nowrap}}
.led{{display:grid;grid-template-columns:52px 62px 74px minmax(0,1fr);gap:10px;padding:4px 4px;
border-bottom:1px solid var(--rule);font-family:ui-monospace,SFMono-Regular,monospace;
font-size:12px;align-items:baseline}}
.led .s{{color:var(--muted)}} .led .k{{color:var(--accent)}} .led .tool{{color:#B45309}}
.led .o{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.led.hl{{background:color-mix(in srgb,#D29922 26%,transparent);outline:1px solid #D29922;
outline-offset:-1px}}
.rc{{color:#2E7D32}} .rc.bad{{color:#C62828}}
.flag{{color:#B26A00;font-size:11px;margin-left:8px;font-family:ui-sans-serif,system-ui,sans-serif}}
.cost{{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px}}
.cost .c{{border:1px solid var(--rule);border-radius:6px;padding:10px 12px}}
.cost .n{{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}}
.cost .l{{font-size:12px;color:var(--muted);margin-top:2px}}
.note{{color:var(--muted);font-size:13px;margin:14px 0 0;max-width:72ch}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12.5px;color:var(--muted);margin:10px 0 0}}
.legend b{{font-family:ui-monospace,monospace;margin-right:3px}}
</style></head><body><div class="wrap">
<h1>{esc(title)}</h1>
<p class="sub">{head}</p>
<input type="radio" name="tab" id="t-report" class="tabin" checked>
<input type="radio" name="tab" id="t-claims" class="tabin">
<input type="radio" name="tab" id="t-ledger" class="tabin">
<input type="radio" name="tab" id="t-cost" class="tabin">
<div class="tabs">
  <label for="t-report" tabindex="0">Report</label>
  <label for="t-claims" tabindex="0">Claims <span class="cnt">{len(verdicts)}</span></label>
  <label for="t-ledger" tabindex="0">Ledger <span class="cnt">{len(ledger)}</span></label>
  <label for="t-cost" tabindex="0">Cost</label>
</div>
<div class="pane" id="p-report"><div class="report">{marked or '<span class="note">no report text</span>'}</div>
<div class="legend"><span><b style="color:{_HEX[Verdict.CONFIRMED]}">✓</b>confirmed</span>
<span><b style="color:{_HEX[Verdict.CONTRADICTED]}">✗</b>contradicted</span>
<span><b style="color:{_HEX[Verdict.UNWITNESSED]}">?</b>unwitnessed</span>
<span><b style="color:{_HEX[Verdict.UNRECORDED]}">○</b>unrecorded</span>
<span><b style="color:{_HEX[Verdict.QUALIFIED]}">≈</b>qualified</span></div>
<p class="note">An unmarked sentence is unmarked on purpose. Opinions, plans and questions are not
claims about work done, and marking them would train you to ignore the marks.</p></div>
<div class="pane" id="p-claims">{''.join(crows) or '<p class="note">no claims</p>'}</div>
<div class="pane" id="p-ledger">{''.join(lrows) or '<p class="note">no events</p>'}</div>
<div class="pane" id="p-cost"><div class="cost">{ccards}</div>{cnote}</div>
</div>
</body></html>
"""


def _cell(text: str, limit: int = 160) -> str:
    """Markdown table cells cannot hold newlines or bare pipes.

    The text is verbatim agent output rendered into GitHub-flavoured Markdown, which passes HTML
    through: `</table>` or an `<img>` in a claim would deform or hide the very rows meant to hold
    that agent honest, so angle brackets are escaped too.
    """
    flat = " ".join(text.split())
    if len(flat) > limit:
        flat = flat[: limit - 1].rstrip() + "…"
    return flat.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def _cited(record: VerdictRecord, ledger: Sequence[LedgerEvent]) -> str:
    """Name the evidence, not just its line number: a reviewer should not have to open the log."""
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


def _coverage_line(coverage: Sequence[Coverage]) -> str:
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
    claims: Sequence[Claim],
    verdicts: Sequence[VerdictRecord],
    ledger: Sequence[LedgerEvent],
    *,
    pr_url: str | None = None,
    coverage: Sequence[Coverage] | None = None,
    receipt_url: str | None = None,
) -> str:
    """The Action's comment (sketch B). Contradictions first: a reviewer reads the worst news first.

    Carries the marker so the Action edits one comment instead of stacking, and the integrity line
    so a reader can tell a complete record from a partial one (invariant 7).
    """
    by_id = {c.id: c for c in claims}
    counts = {v: sum(1 for r in verdicts if r.verdict is v) for v in Verdict}
    headline = (
        f"**Receipt for this PR's description** · {len(verdicts)} "
        f"claim{'s' if len(verdicts) != 1 else ''} · source: {session.agent} "
        f"session `{session.id[:12]}`"
    )
    integrity = (
        f"ledger `{session.ledger_root_hash[:8]}` · {session.n_events} events · "
        f"integrity {session.integrity_score:.2f}"
    )

    lines = [MARKER, headline, "", integrity, ""]
    if not verdicts:
        lines.append("_No claims found in the description: nothing to check._")
        return "\n".join(lines) + "\n"

    lines += ["| | Claim | Evidence | Tier |", "|---|---|---|---|"]
    for verdict in ORDER:
        for record in [r for r in verdicts if r.verdict is verdict]:
            claim = by_id.get(record.claim_id)
            rationale = _cell(record.rationale, 80)
            if record.qualifier:
                qualifier = _cell(record.qualifier, 60)
                rationale = f"{rationale} ({qualifier})" if rationale else qualifier
            evidence = _cited(record, ledger)
            detail = f"{evidence}<br>{rationale}" if rationale else evidence
            lines.append(
                f"| {MARK[verdict][0]} **{verdict.value}** | "
                f"{_cell(claim.text if claim else record.claim_id)} | {detail} | "
                f"{record.tier} · {record.method} |"
            )

    lines += ["", " · ".join(f"{counts[v]} {MARK[v][0]}" for v in ORDER if counts[v])]
    line = _coverage_line(coverage or [])
    if line:
        lines += ["", line]
    if counts[Verdict.CONTRADICTED]:
        lines += [
            "",
            "> A contradicted claim means the log carries positive evidence against it "
            "(a failing exit code, a command that never ran, a file that is not there). "
            "Unwitnessed is not an accusation: the record simply does not say.",
        ]
    if counts[Verdict.OUT_OF_SCOPE]:
        lines += [
            "",
            "> `out_of_scope` means an action reached outside what this session was asked to "
            "touch (SCOPE.md §4) -- a boundary violation, not evidence a claim is false.",
        ]
    footer = []
    if receipt_url:
        footer.append(f"[Open full receipt]({receipt_url})")
    if pr_url:
        footer.append(f"[PR]({pr_url})")
    footer.append("generated by `custos-code pr-comment`")
    lines += ["", f"<sub>{' · '.join(footer)}</sub>"]
    return "\n".join(lines) + "\n"
