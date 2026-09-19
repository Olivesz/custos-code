"""The Action's only real logic is `.github/pr_bundle.jq`; run it here, not first in production.

It turns the GitHub REST shapes (`pulls/{n}`, its commits, its files, the head SHA's check runs)
into the class-R bundle the Devin and Copilot adapters read, so a PR with no session log still
gets a receipt whose shell claims are `unrecorded` and whose edit claims are settleable.
"""

import json
import os
import shutil
import subprocess

import pytest

from receipts import adapters, claims, verdicts
from receipts.models import Verdict
from receipts.report import MARKER, pr_comment

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROGRAM = os.path.join(ROOT, ".github", "pr_bundle.jq")
API = os.path.join(ROOT, "tests", "golden", "action", "github_api.json")

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq is what the Action runs")


@pytest.fixture(scope="module")
def bundle(tmp_path_factory) -> dict:
    payload = json.load(open(API, encoding="utf-8"))
    tmp = tmp_path_factory.mktemp("api")
    args = []
    for name, key in (
        ("pr", "pr"),
        ("commits", "commits"),
        ("checks", "check_runs"),
        ("files", "files"),
    ):
        path = tmp / f"{name}.json"
        path.write_text(json.dumps(payload[key]))
        args += ["--slurpfile", name, str(path)]
    out = subprocess.run(
        ["jq", "-n", *args, "-f", PROGRAM], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def test_bundle_keeps_the_pr_body_as_the_report_and_the_commits_as_evidence(bundle) -> None:
    assert bundle["pull_request"]["body"].startswith("Adds `retry()`")
    assert [c["subject"] for c in bundle["commits"]] == [
        "chore: scaffold the retry helper",
        "feat: retry uploads on 502",
    ]
    assert bundle["commits"][-1]["files"] == ["src/widgets/upload.py", "tests/unit/test_upload.py"]
    assert bundle["checks"][0]["conclusion"] == "failure"
    assert "1 failed, 40 passed" in bundle["checks"][0]["output"]


def test_both_class_r_adapters_can_read_the_same_bundle(tmp_path, bundle) -> None:
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle))
    for agent in ("devin", "copilot"):
        session, ledger, report = adapters.parse(str(path), agent)
        assert report and "retry()" in report
        assert session.integrity_score < 1.0  # no tool log: say so in the score
        assert verdicts.known_incomplete(ledger)
        assert any(e.tool == "Git" and e.paths for e in ledger)
        assert any(e.tool == "CI" and e.flags.error for e in ledger)


def test_the_receipt_the_action_would_post(tmp_path, bundle) -> None:
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle))
    session, ledger, report = adapters.parse(str(path), "devin")
    assert report is not None
    found = claims.extract(report, session.id)
    records = verdicts.run(found, ledger, None)
    body = pr_comment(session, found, records, ledger, pr_url=bundle["pull_request"]["html_url"])
    assert body.startswith(MARKER) and "integrity" in body
    # "ran pytest locally" has no witness here and the record admits it
    assert any(r.verdict is Verdict.UNRECORDED for r in records)
    assert Verdict.CONFIRMED.value in body or "unrecorded" in body
