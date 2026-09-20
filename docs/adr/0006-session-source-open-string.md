# `Session.source` is an open string, not a closed `Literal`

**Status:** accepted (2026-09-19)

**Context and decision.** `Session.source` named every adapter in a closed `Literal`
(`"claude_code" | "codex" | "copilot" | "devin" | "otel"`) in `models.py`, a shared seam that
needs review from all three people. Every new adapter (`machine`, and whatever comes after it)
therefore had to touch that line, dragging an otherwise single-owner adapter PR into a
three-approval shared-seam change every time — a toll paid repeatedly for no safety this
particular field actually needs (nothing in the codebase branches on an exhaustive match over
`Session.source`; it is read, stored, and displayed, never switched on). `source` is now a plain
`str`: an adapter names itself however it likes, and `models.py` stops being a per-adapter
bottleneck.

**Consequences.** A typo in an adapter's own `source=` string is no longer caught by
`mypy --strict`; it would show up as an unrecognized value wherever sessions are grouped or
displayed, which is an acceptable trade against the review friction removed. `Claim.source`
(`Literal["report", "plan", "request"]`) is unaffected — that vocabulary is small, fixed, and not
tied to how many adapters exist, so it stays closed.
