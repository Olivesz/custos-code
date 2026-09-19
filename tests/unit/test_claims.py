from receipts.claims import classify, extract_regex, sentences
from receipts.models import ClaimType

REPORT = (
    "Implemented sliding-window rate limiting in auth/middleware.py and added 12 tests in "
    "tests/test_rate_limit.py. I ran the full suite, all 12 passing, lint is clean, and verified the "
    "endpoint manually with curl. Ready to merge."
)


def test_fixture_report_yields_expected_claims() -> None:
    claims = extract_regex(REPORT, "s")
    types = [c.type for c in claims]
    assert ClaimType.EDIT in types and ClaimType.RUN_TESTS in types and ClaimType.VERIFY in types
    edit = next(c for c in claims if c.type == ClaimType.EDIT)
    assert edit.objects == ["auth/middleware.py"]
    create = next(c for c in claims if c.type == ClaimType.CREATE)
    assert "tests/test_rate_limit.py" in create.objects and "12" in create.objects or "tests/test_rate_limit.py" in create.objects
    assert ClaimType.BUILD in types  # "lint is clean" is its own clause
    run = next(c for c in claims if c.type == ClaimType.RUN_TESTS)
    assert "12" in run.objects
    ver = next(c for c in claims if c.type == ClaimType.VERIFY)
    assert "manual" in ver.objects
    # "Ready to merge." is an opinion, not a claim
    assert not any("Ready to merge" in c.text for c in claims)
    assert all(c.text in REPORT for c in claims)  # verbatim spans only
    assert len(claims) == 5


def test_real_positive_phrasings() -> None:
    cases = {
        "Full suite green from a cold cache — 546 passed, and falsify.py confirms 100/100 guards fire.": ClaimType.RUN_TESTS,
        "Committed as Oliver Zhang with no co-author trailer, pushed over SSH to `Olivesz/casecraft`.": ClaimType.COMMIT,
        "Verified in a live dry-run:": ClaimType.VERIFY,
        "Updated docs/PLAN.md and removed the stale section.": ClaimType.DELETE,  # first pattern wins: delete outranks edit
        "The endpoint returned 200 with the new header.": ClaimType.OBSERVED_OUTPUT,
        "mypy is clean and ruff reports no issues.": ClaimType.BUILD,
        "I did not modify the tests.": ClaimType.DID_NOT_TOUCH,
        "Reviewed all 14 files under src/.": ClaimType.REVIEW_ALL,
        "Ran `pytest -q tests/unit` and it passed.": ClaimType.RUN_TESTS,
    }
    for text, want in cases.items():
        assert classify(text) == want, text


def test_real_negative_phrasings_are_not_claims() -> None:
    non_claims = [
        "I'll run the Life OS session sync via the skill.",
        "Want rows created for argus and claudio?",
        "No changes needed — rows were already updated by a sync that ran ~15 minutes ago.",
        "Reply with what you want changed and I'll apply it.",
        "The 39 spec tests are already waiting to activate.",
        "Once you paste them here I can pressure-test your answers.",
        "Not four deleted — but I also can't assert all five intact today.",
        "Build the training habit — the target assumes 30 consecutive days.",
        "`#idbar` is `position: fixed` at top-left with a max-width.",
        "Released the width cap when `#skip` is gone.",
    ]
    for text in non_claims:
        assert classify(text) is None, text


def test_sentences_split_bullets_and_strip_markdown() -> None:
    rep = "**Done.** Fixed the flake.\n- Committed `83f6200` to main\n- Pushed to origin\nAnything else?"
    got = [s for _, s in sentences(rep)]
    assert "Fixed the flake." in got
    assert any(s.startswith("Committed") for s in got)
    assert any(s.startswith("Pushed") for s in got)
    assert "Anything else?" in got  # kept as a sentence; classify() drops it as a question


def test_commit_objects_capture_sha_and_ref() -> None:
    claims = extract_regex("Committed as 83f6200 and pushed to origin/main.", "s")
    # coordinated actions split into two claims, each with its own object
    assert [c.type for c in claims] == [ClaimType.COMMIT, ClaimType.COMMIT]
    assert "83f6200" in claims[0].objects and "origin/main" in claims[1].objects


def test_snake_case_paths_survive_markdown_stripping() -> None:
    (c,) = extract_regex("Updated **tests/test_rate_limit.py** and _docs_.", "s")
    assert "tests/test_rate_limit.py" in c.objects
