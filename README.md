# Custos Code

Checks a coding agent's final report against the log of what it actually did.

An agent finishes and says "implemented the feature, ran the tests, all passing." Custos Code reads the harness-written action log, splits the report into claims, and marks each one **confirmed**, **contradicted**, **unwitnessed**, **unrecorded**, or **qualified**, with the ledger lines that back the verdict. Contradictions go back to the agent before it is allowed to stop.

```
custos-code  session 4f2a… · 63 events
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

## See it work

```bash
uv sync
export OPENAI_API_KEY=...
uv run custos-code demo                 # the whole loop on a known trap, live
uv run custos-code demo --scenario honest   # the control: nothing blocks
uv run custos-code check --last         # your own most recent session
```

`demo` prints five things from the fixture's own tool log: what was asked, what the agent actually
did, what it said, the receipt, and the deterministic nudge that goes back. `custos-code check --format html --out card.html` writes a self-contained report card --
the claims, the ledger they cite, and what the check cost, with no JavaScript in it.
`--format markdown` writes what the PR bot posts. Both are options on `check`, not `demo`.

## Prototype

`docs/prototype/index.html` is an interactive, non-functional mock of the editor experience: marks on the agent's message, the evidence panel, editor decorations, the auto-mode loop, and the PR receipt. It is a single self-contained file:

```bash
open docs/prototype/index.html          # macOS
# or: python3 -m http.server -d docs/prototype 8765  →  http://localhost:8765
```

## Local setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) once. On macOS
with Homebrew: `brew install uv`. From the repository directory:

```bash
uv python install
make sync
make check
uv run custos-code check --last
```

`.python-version` selects Python 3.12, independently of your shell's pyenv or
Conda default. `make sync` installs the project and developer tools from the
committed `uv.lock`; it fails if the lockfile needs updating. After an intentional
dependency change, run `uv lock` and include the lockfile in the same PR.

`make build` produces a wheel and source distribution in `dist/`. CI runs the
same checks, installs both distributions in clean environments, and checks the
installed CLI outside the source checkout. CI uses locked installs following
the [uv integration guide](https://docs.astral.sh/uv/guides/integration/github/).

## Bench container

With Docker installed and running, build from the repository root:

```bash
docker build -t custos-code-bench .
docker run --rm --network none custos-code-bench
docker run --rm --network none custos-code-bench python -m pytest --version
```

The image contains Python 3.12, uv 0.12.17, git, the installed Custos Code package,
developer dependencies, and scenario descriptions under `/app/bench/scenarios`.
It runs as a non-root user in writable `/workspace`; the default command shows
CLI help. The benchmark orchestration and fixture repos are not implemented
yet, so this is their execution environment, not a working benchmark command.
Agent CLIs and their credentials are not installed.

The Docker build context is an allowlist that excludes local session logs,
credentials, caches, and git history. For a local Python fixture, mount only
that fixture (including its git metadata when state checks need it):

```bash
docker run --rm --network none \
  --mount type=bind,src="$(pwd)/path/to/fixture",dst=/workspace,readonly \
  custos-code-bench python -m pytest -p no:cacheprovider
```

This read-only example suits tests that do not write into the fixture. Agent
bench runs will need a disposable writable checkout and explicit network and
credential configuration. Non-Python runners require additional toolchains.

## Why

Across 20,574 real coding-agent sessions, 22.58% of 16,118 validated misalignment episodes were the agent misreporting its own work, and only 2.99% of resolved episodes were self-corrected. Every agent vendor attaches an action log; none checks the report against it. Sources and denominators: `docs/RESEARCH.md`.

## Working in this repo

Read `AGENTS.md`. Flag anything undecided with `NEEDS-DECISION(owner):`. Local by default; secrets are redacted at ingest; fixtures are synthetic.

## License

MIT (see `LICENSE`).
