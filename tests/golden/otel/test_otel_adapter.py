"""OTel GenAI spans: content capture is opt-in, so an uncaptured result is a stated gap."""

import json
import os

from receipts.adapters import otel
from receipts.ledger import verify_chain
from receipts.models import EventKind

SPANS = os.path.join(os.path.dirname(__file__), "spans.json")


def test_otlp_json_parses_into_a_ledger() -> None:
    sess, ledger, report = otel.parse(SPANS)
    assert sess.source == "otel" and sess.id == "otel-sess-42"
    assert sess.agent == "acme-agent" and sess.model == "gpt-5" and sess.cwd == "/srv/acme"
    assert verify_chain(ledger)
    assert report == "Fixed the parser and ran the tests; all 12 pass."
    assert [e.kind for e in ledger] == [
        EventKind.TEXT,
        EventKind.CALL,
        EventKind.RESULT,
        EventKind.CALL,
        EventKind.RESULT,
    ]


def test_tool_names_normalise_and_exit_code_and_pipes_survive() -> None:
    _, ledger, _ = otel.parse(SPANS)
    shell = next(e for e in ledger if e.tool == "Bash" and e.kind is EventKind.RESULT)
    assert shell.exit_code == 0 and shell.duration_ms == 2500
    assert shell.flags.piped and not shell.flags.stderr_dropped
    assert "12 passed" in (shell.output or "")


def test_uncaptured_result_is_marked_incomplete_and_error_status_is_positive_evidence() -> None:
    sess, ledger, _ = otel.parse(SPANS)
    patch = next(e for e in ledger if e.tool == "Edit" and e.kind is EventKind.RESULT)
    assert patch.flags.stderr_dropped  # no gen_ai.tool.call.result: unrecorded, not confirmed
    assert patch.output is None
    assert patch.exit_code == 1 and patch.flags.error
    assert sess.integrity_score == 0.5  # one of two results carried its content


def test_jsonl_of_bare_spans_is_accepted(tmp_path) -> None:
    with open(SPANS) as fh:
        spans = json.load(fh)["resourceSpans"][0]["scopeSpans"][0]["spans"]
    path = tmp_path / "spans.jsonl"
    path.write_text("\n".join(json.dumps(s) for s in spans))
    sess, ledger, report = otel.parse(str(path))
    assert sess.id == "otel-sess-42" and len(ledger) == 5 and report is not None


def test_unset_status_without_an_exit_code_is_not_success(tmp_path) -> None:
    span = {
        "traceId": "t",
        "spanId": "s",
        "name": "execute_tool bash",
        "startTimeUnixNano": "1758276010000000000",
        "endTimeUnixNano": "1758276011000000000",
        "status": {"code": "STATUS_CODE_UNSET"},
        "attributes": [
            {"key": "gen_ai.tool.name", "value": {"stringValue": "bash"}},
            {"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "make deploy"}},
        ],
    }
    path = tmp_path / "one.json"
    path.write_text(json.dumps(span))
    _, ledger, _ = otel.parse(str(path))
    result = next(e for e in ledger if e.kind is EventKind.RESULT)
    assert result.exit_code is None and not result.flags.error
