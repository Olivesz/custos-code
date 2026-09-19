# Working together in this repo

Three people, mostly agent-written code, one weekend. Conflicts come from two people (or two agents) touching the same file in the same hour. The rules below make that rare, and make it cheap when it happens.

## 1. Ownership is by path, and areas are disjoint
`.github/CODEOWNERS` maps every path to one owner. You may read anything; you write only inside your paths. If you need a change elsewhere, open a small PR against that path and tag the owner, or ask them to make it. Shared seams (`src/receipts/models.py`, `AGENTS.md`, `docs/OPEN_QUESTIONS.md`, `docs/adr/`, `pyproject.toml`) change only through a PR reviewed by both other people.

## 2. The seams are frozen unless a PR says otherwise
- `models.py` is the contract between everyone. Adding a field is a PR with both reviewers; renaming or removing one is an ADR.
- Adapters return `(Session, list[LedgerEvent], report_text | None)`. Nothing downstream knows which agent produced the ledger.
- Rules return `VerdictRecord | None` (None means "escalate"). The judge cannot return `contradicted`.
- Tests are the interface documentation: if you change behaviour, change the golden file or fixture in the same PR.

## 3. Branches and PRs
- Never commit to `main`. Branch names: `<name>/<area>-<short>` (`anush/parsers-pytest`, `ananya/adapter-codex`).
- One PR per concern, under ~400 lines of diff where possible. Big features land as a sequence of small PRs behind a `NotImplementedError` or a config flag, not one giant PR at hour 20.
- Rebase on `main` before opening and before merging: `git fetch origin && git rebase origin/main`. Squash-merge; the PR title becomes the commit message.
- CI must be green. **Request both other people on every PR, not just the path's owner** (`gh pr create --reviewer anushmainali,AnanyaGuntur8`, minus yourself). One approval from either still merges a PR inside one person's own paths; shared seams still need both. This is about latency, not ceremony -- whoever is awake unblocks it, instead of the PR waiting on one named person. Do not rely on CODEOWNERS to request for you: it requests only the last matching pattern's owners, and on PR #44 it silently requested one of the two.
- During the event, the review can be a 2-minute look at the diff and the receipt; it still happens.
- Draft PRs early. Pushing a half-done branch is fine; opening a draft PR the moment a branch exists tells the others what you are touching.

## 4. Commits
- Conventional commits: `feat:`, `fix:`, `docs:`, `bench:`, `eval:`, `ci:`, `refactor:`, `test:`.
- Real name and email, one identity per person, no tool-attribution trailers. Check once per clone: `git config user.email`.
- Commit often locally; squash on merge.

## 5. Local checks before every push
```
make check        # ruff, mypy --strict, pytest
make fix          # ruff --fix and format
```
`pre-commit install` once per clone runs the fast subset on every commit.

## 6. Agents working in this repo
Everything above applies to an agent session too. Additionally:
- Start the session on a branch named for the owner and area; state the branch in the first message.
- Read `AGENTS.md`, then only the module docstrings for the area being touched.
- Do not "tidy up" files outside the area. Do not resolve someone else's `NEEDS-DECISION` tag.
- When the session ends, the PR description's "Receipt" section must contain the actual `make check` output, not a summary of it.

## 7. Coordination that takes zero tools
- One shared message thread per day with three lines each: what merged, what is in flight (branch names), what is blocked.
- Before touching a shared seam, say so in the thread first.
- If two branches collide, the later one rebases; the earlier one does not rewrite history.

## 8. Decisions
- Small: edit `docs/OPEN_QUESTIONS.md` in your PR.
- Non-obvious: add `docs/adr/NNNN-title.md` (context, decision, consequences) in the same PR.
- Disputed: the path owner decides; product-wide semantics (verdicts, invariants) are Oliver's call, as `docs/PLAN.md` says.
