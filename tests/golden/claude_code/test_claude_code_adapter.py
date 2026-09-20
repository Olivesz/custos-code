import json
import os

from custos_code.adapters import claude_code
from custos_code.ledger import verify_chain
from custos_code.models import EventKind

HERE = os.path.dirname(__file__)
FIXTURE = os.path.join(HERE, "session.jsonl")
EXPECTED = os.path.join(HERE, "expected_ledger.jsonl")


def _strip(e: dict) -> dict:
    # hashes are derived; compare the substantive fields
    e = dict(e)
    e.pop("hash", None)
    e.pop("prev_hash", None)
    return e


def test_parse_matches_golden() -> None:
    sess, ledger, report = claude_code.parse(FIXTURE)
    assert sess.source == "claude_code"
    assert sess.cwd == "/home/dev/rate-limiter"
    assert sess.git_branch == "feat/rate-limit"
    assert verify_chain(ledger)
    assert report is not None and report.startswith("Implemented sliding-window rate limiting")
    got = [_strip(json.loads(e.model_dump_json())) for e in ledger]
    with open(EXPECTED) as fh:
        want = [_strip(json.loads(line)) for line in fh if line.strip()]
    assert got == want


def test_flags_and_redaction() -> None:
    _, ledger, _ = claude_code.parse(FIXTURE)
    calls = {e.seq: e for e in ledger if e.kind == EventKind.CALL}
    results = [e for e in ledger if e.kind == EventKind.RESULT]
    # the piped lint and test commands are flagged on their results
    piped = [e for e in results if e.flags.piped]
    assert len(piped) == 2
    # paths are resolved from Edit/Write/Read inputs
    assert any(p.endswith("/auth/middleware.py") for c in calls.values() for p in c.paths)
    # the API key in a Bash command is redacted before storage and hashing
    assert not any("sk-abcdefghij" in json.dumps(e.input or {}) for e in ledger)
    assert any("[REDACTED:env]" in json.dumps(e.input or {}) for e in calls.values())
    # sidechain never set in this fixture
    assert not any(e.flags.sidechain for e in ledger)


def test_find_last_session(tmp_path) -> None:
    d = tmp_path / "proj"
    d.mkdir()
    a, b = d / "a.jsonl", d / "b.jsonl"
    a.write_text("{}\n")
    b.write_text("{}\n")
    os.utime(a, (1, 1))
    assert claude_code.find_last_session(str(tmp_path)).endswith("b.jsonl")
