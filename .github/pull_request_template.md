## What

One sentence. Link the open question or bench trap it serves (e.g. `E9`, `piped-runner`).

## Area

- [ ] Stays inside my owned paths (see `.github/CODEOWNERS`), or the owner is tagged below.
- [ ] Touches a shared seam (`models.py`, `AGENTS.md`, `OPEN_QUESTIONS.md`, `pyproject.toml`) → both other people requested as reviewers.

## Checks

- [ ] `make check` passes locally (ruff, mypy --strict, pytest).
- [ ] If a verdict can change: a gold-set or fixture case was added or updated.
- [ ] No secrets, no real customer data, no tool-attribution lines in commits.
- [ ] `docs/OPEN_QUESTIONS.md` updated if this decides or raises a question.

## Receipt

Paste the relevant test or command output. An agent's summary is a claim; this is the evidence.
