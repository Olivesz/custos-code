# The LLM judge cannot emit `contradicted`

**Status:** accepted (2026-09-19)

**Context and decision.** A false accusation is the worst failure and an LLM verdict varies run to run. Blocking rests only on rules, re-runs, and state checks. The judge emits confirmed or unwitnessed, at temperature 0, with citations, majority of 3.

**Consequences.** See AGENTS.md invariants and docs/DESIGN.md §16.
