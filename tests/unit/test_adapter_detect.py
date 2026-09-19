"""`receipts check <path>` should not need --agent: the file's own shape names the adapter."""

import json
import os

import pytest

from receipts import adapters

GOLDEN = os.path.join(os.path.dirname(os.path.dirname(__file__)), "golden")


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("claude_code/session.jsonl", "claude_code"),
        ("codex/session.jsonl", "codex"),
        ("devin/bundle.json", "devin"),
        ("copilot/bundle.json", "copilot"),
        ("otel/spans.json", "otel"),
    ],
)
def test_detect_names_the_adapter_for_every_golden_fixture(fixture: str, expected: str) -> None:
    assert adapters.detect(os.path.join(GOLDEN, fixture)) == expected


def test_detect_machine_log(tmp_path) -> None:
    path = tmp_path / "host-2025-09-19.jsonl"
    path.write_text(
        json.dumps(
            {
                "recorder": "receipts-machine",
                "v": 1,
                "event": "end",
                "ts": 1.0,
                "cmd": "ls",
                "exit": 0,
            }
        )
        + "\n"
    )
    assert adapters.detect(str(path)) == "machine"


def test_parse_routes_through_the_registry() -> None:
    sess, ledger, _ = adapters.parse(os.path.join(GOLDEN, "codex/session.jsonl"))
    assert sess.source == "codex" and ledger
    forced, _, _ = adapters.parse(os.path.join(GOLDEN, "devin/bundle.json"), "devin")
    assert forced.source == "devin"


def test_unknown_or_empty_input_asks_for_the_flag(tmp_path) -> None:
    empty, junk = tmp_path / "e.jsonl", tmp_path / "j.jsonl"
    empty.write_text("")
    junk.write_text(json.dumps({"hello": "world"}) + "\n")
    with pytest.raises(ValueError, match="is empty"):
        adapters.detect(str(empty))
    with pytest.raises(ValueError, match="pass --agent"):
        adapters.detect(str(junk))
    with pytest.raises(ValueError, match="unknown agent"):
        adapters.parse(str(junk), "cursor")


def test_every_registered_adapter_satisfies_the_contract() -> None:
    assert set(adapters.ADAPTERS) == {"claude_code", "codex", "devin", "copilot", "machine", "otel"}
    assert all(callable(a.parse) for a in adapters.ADAPTERS.values())
