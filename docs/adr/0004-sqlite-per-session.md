# SQLite per session, DuckDB across sessions

**Status:** accepted (2026-09-19)

**Context and decision.** One file per session keeps ingest lock-free and local-by-default trivial. DuckDB reads the files for prevalence and cost analysis.

**Consequences.** See AGENTS.md invariants and docs/DESIGN.md §16.
