# Normalises the GitHub REST shapes into the class-R bundle the Devin/Copilot adapters read
# (see src/receipts/adapters/state.py). Used by .github/workflows/receipt.yml when the PR carries
# no session log; tested in tests/unit/test_pr_bundle_jq.py so the Action's one piece of logic is
# not first exercised in production.
#
# Inputs: $pr (pulls/{n}), $commits (pulls/{n}/commits), $checks (check_runs), $files (pulls/{n}/files).
# The PR's changed files are attached to the head commit: the file list is per-PR, not per-commit,
# and the head commit is the one whose tree actually contains them.
($commits[0] // []) | map({
  sha: .sha,
  subject: ((.commit.message // "") | split("\n")[0]),
  ts: (.commit.committer.date // .commit.author.date),
  body: (.commit.message // ""),
  files: []
}) as $c
| (($files[0] // []) | map(.filename)) as $paths
| (if ($c | length) > 0 then ($c[:-1] + [$c[-1] + {files: $paths}])
   else (if ($paths | length) > 0
         then [{sha: $pr[0].head.sha, subject: $pr[0].title, ts: $pr[0].updated_at, files: $paths}]
         else [] end)
   end) as $commit_rows
| (($checks[0] // []) | map({
    name: .name,
    conclusion: (.conclusion // .status),
    started_at: .started_at,
    completed_at: .completed_at,
    url: .html_url,
    output: ((.output.summary // "") + (if (.output.text // "") == "" then "" else "\n" + .output.text end))
  })) as $check_rows
| ($pr[0] | {number, title, body, url: .html_url, html_url, head: .head.ref, user: .user}) as $pull
| {
    pull_request: $pull,
    commits: $commit_rows,
    checks: $check_rows,
    session: {
      id: ("pr-" + ($pr[0].number | tostring)),
      session_id: ("pr-" + ($pr[0].number | tostring)),
      agent: $pr[0].user.login,
      status_enum: $pr[0].state,
      created_at: $pr[0].created_at,
      pull_requests: [$pull]
    },
    git: {branch: $pr[0].head.ref, commits: $commit_rows}
  }
