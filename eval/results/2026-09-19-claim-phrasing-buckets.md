# Claim-phrasing buckets: sentences the regex baseline does not flag

Source: local (7 session(s) with a final report).

**Not the SWE-chat corpus issue #27 asks for.** SWE-chat needs a Hugging Face account that has accepted `SALT-NLP/SWE-chat`'s gated terms plus an `HF_TOKEN`; this run is a local smoke test of the bucketing tool only, on whatever transcripts already exist on this machine. Re-run with `--source swechat` (see `download_swechat.py`) once a token is available, before proposing anything to `claims.py` for real.

| verb | count | example |
|---|---|---|
| summary | 3 | Summary of changes in `src/custos_code/rerun.py`: |
| t | 2 | It now overlays only `git ls-files` (tracked) + `git ls-files --others --exclude-standard` (untracked-unignored) paths onto the HEAD worktree, and removes any t |
| just | 2 | the implementation just didn't match it until now). |
| now | 2 | Now let's run the mining script against the local sessions as a smoke test and generate the results doc. |
| branch | 1 | Branch `anush/issue-22-trailer-off-stdout` is now on `origin`, tracking set up. |
| github's | 1 | GitHub's suggested PR link: https://github.com/Olivesz/custos-code/pull/new/anush/issue-22-trailer-off-stdout — let me know if you want me to open the PR too. |
| mplementation | 1 | Implementation is done |
| materialize | 1 | `materialize_worktree` no longer does a blanket `shutil.copytree`. |
| make | 1 | `make check` (ruff |
| not | 1 | Not committed — let me know if you want it committed/pushed, and whether you'd like the `OPEN_QUESTIONS.md` E3 row's wording touched up to match (it already des |
| test | 1 | `test_parsers.py` (16 tests) — each of the 5 runner parsers (pytest, jest, vitest, go test, cargo) covering both a matching case and a none-match case, includin |
| plus | 1 | plus the `parse()` dispatcher's priority order and its none-match fallback. |
| src | 1 | `src/custos_code/cost.py` — `SessionCost` built from three independent sources (no re-derivation): `VerdictRecord.tier`/`.method` for claim counts, ledger `RERUN`  |
| load | 1 | `load_prices()` reads `[prices.<model-id>]` from `~/.custos-code/config.toml`, each entry dated with `asof` |
| missing | 1 | missing file/model prices at $0.00 rather than crashing. |
| render | 1 | `render_table()` and `to_dict()` back the `custos-code cost` command. |
| tracks | 1 | Tracks its own `Usage` (requests/input_tokens/tokens_saved) the same way `judge.Usage` does. |
| config | 1 | `config.toml` / `config.example.toml` — new `[compress]` (off by default) and `[prices."<model>"]` tables (gpt-5.2, claude-opus-5, bear-2), figures flagged as p |
| this | 1 | This is the one hunk outside my area — flag it for Oliver's review. |
| eval | 1 | `eval/cost_report.py` — the three-arm comparison (frontier / ladder / ladder+bear-2), reporting $ and tokens for real, and kappa honestly marked `"pending"` unt |
| arm | 1 | Arm (c) makes a real bear-2 call on the ladder's actual windowed residue and reports projected dollars from the measured savings |
| nothing | 1 | Nothing else pending — the merge is fully resolved (no unmerged paths, no conflict markers anywhere), and both files consistently reflect Oliver's `#22` correct |
| ready | 1 | Ready for you to run `git commit` to conclude the merge. |
