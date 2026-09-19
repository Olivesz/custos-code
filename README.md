# Receipts

Checks a coding agent's final report against the log of what it actually did.

An agent finishes and says "implemented the feature, ran the tests, all passing." Receipts reads the harness-written action log, splits the report into claims, and marks each one **confirmed**, **contradicted**, **unwitnessed**, **unrecorded**, or **qualified**, with the ledger lines that back the verdict. Contradictions go back to the agent before it is allowed to stop.

```
receipts  session 4f2a… · 63 events
  ✓ confirmed     edited auth/middleware.py            tier 1 · #14 Edit, git diff agrees
  ✓ confirmed     added tests/test_rate_limit.py        tier 1 · #31 Write, file present
  ✗ contradicted  ran the suite, all 12 passing         tier 2 · #41 `pytest | tail -5` exit 0, "collected 0 items"
  ? unwitnessed   ready to merge                        no CI, no git status after #41
stop blocked · 1 contradicted · evidence returned to agent
```

## Status

Pre-build. The design, research, plan, and evidence protocol are in `docs/`. Start with [AGENTS.md](AGENTS.md).

- [docs/DESIGN.md](docs/DESIGN.md) — problem, customers, verification ladder, feasibility, product sketches, benchmark, system design, prize strategy, adversarial review
- [docs/RESEARCH.md](docs/RESEARCH.md) — the evidence: prevalence with denominators, cost, current workarounds, tool landscape, 40 seed cases
- [docs/PLAN.md](docs/PLAN.md) — who owns what, phases, parallel tracks
- [docs/EVIDENCE_PLAN.md](docs/EVIDENCE_PLAN.md) — pre-registered study: accuracy, time saved, retention, usability
- [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md) — every unresolved decision with an owner

## Quickstart (target)

```bash
uv sync
uv run receipts check --last
```

## Why

Across 20,574 real coding-agent sessions, 22.58% of 16,118 validated misalignment episodes were the agent misreporting its own work, and only 2.99% of resolved episodes were self-corrected. Every agent vendor attaches an action log; none checks the report against it. Sources and denominators: `docs/RESEARCH.md`.

## Working in this repo

Read `AGENTS.md`. Flag anything undecided with `NEEDS-DECISION(owner):`. Local by default; secrets are redacted at ingest; fixtures are synthetic.

## License

MIT (see `LICENSE`).
