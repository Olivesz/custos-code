"""twopass._ask must speak whichever SDK shape backend.client() actually returns.

No network: fake clients stand in for openai's Responses API and anthropic's Messages API,
the two shapes judge.py's OpenAIBackend and AnthropicBackend each wrap for real.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from custos_code.twopass import Checked, _ask, run

_SCHEMA: dict[str, Any] = {"type": "object", "properties": {"x": {"type": "string"}}}


@dataclass
class _FakeOpenAIBackend:
    """`client().responses.create(...)` -- the shape twopass._ask used to assume for everyone."""
    payload: dict[str, Any]
    judge_model: str = "gpt-x"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def client(self) -> Any:
        backend = self

        class _Responses:
            def create(self, **kwargs: Any) -> Any:
                backend.calls.append(kwargs)
                return SimpleNamespace(output_text=json.dumps(backend.payload),
                                       usage=SimpleNamespace(input_tokens=7))

        return SimpleNamespace(responses=_Responses())


@dataclass
class _FakeAnthropicBackend:
    """`client().messages.create(...)` -- what a real AnthropicBackend.client() returns.

    Its client has no `.responses` attribute at all, which is exactly what made the old
    unconditional `backend.client().responses.create(...)` raise AttributeError here.
    """
    payload: dict[str, Any]
    judge_model: str = "claude-x"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def client(self) -> Any:
        backend = self

        class _Messages:
            def create(self, **kwargs: Any) -> Any:
                backend.calls.append(kwargs)
                block = SimpleNamespace(type="text", text=json.dumps(backend.payload))
                return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=9))

        return SimpleNamespace(messages=_Messages())


def test_ask_uses_the_openai_responses_shape_when_the_client_has_it() -> None:
    backend = _FakeOpenAIBackend({"x": "ok"})
    usage = Checked()

    result = _ask(backend, "sys", "user", _SCHEMA, "claims", usage)

    assert result == {"x": "ok"}
    assert usage.requests == 1 and usage.input_tokens == 7
    assert backend.calls[0]["text"]["format"]["schema"] == _SCHEMA


def test_ask_falls_back_to_the_anthropic_messages_shape() -> None:
    """This is the crash this test guards against: no `.responses` on an Anthropic-shaped client."""
    backend = _FakeAnthropicBackend({"x": "ok"})
    usage = Checked()

    result = _ask(backend, "sys", "user", _SCHEMA, "claims", usage)

    assert result == {"x": "ok"}
    assert usage.requests == 1 and usage.input_tokens == 9
    assert "Reply with JSON matching this schema" in backend.calls[0]["messages"][0]["content"]


def test_ask_handles_anthropic_prose_wrapped_around_the_json() -> None:
    """Anthropic has no strict-schema mode, so the reply can carry prose around the JSON object."""

    class _WrappedProseBackend(_FakeAnthropicBackend):
        def client(self) -> Any:
            class _Messages:
                def create(self, **kwargs: Any) -> Any:
                    block = SimpleNamespace(
                        type="text", text='Sure, here you go:\n{"x": "ok"}\nHope that helps!')
                    return SimpleNamespace(content=[block], usage=None)

            return SimpleNamespace(messages=_Messages())

    result = _ask(_WrappedProseBackend({}), "sys", "user", _SCHEMA, "claims", Checked())
    assert result == {"x": "ok"}


class _DecomposeThenCheckBackend:
    """DECOMPOSE (schema name "claims") and CHECK (schema name "check") get different answers."""

    judge_model = "gpt-x"

    def __init__(self, claims: list[dict[str, Any]], check_verdict: dict[str, Any]) -> None:
        self._claims = claims
        self._check_verdict = check_verdict
        self.check_calls = 0

    def client(self) -> Any:
        backend = self

        class _Responses:
            def create(self, **kwargs: Any) -> Any:
                name = kwargs["text"]["format"]["name"]
                if name == "claims":
                    payload: dict[str, Any] = {"claims": backend._claims}
                else:
                    backend.check_calls += 1
                    payload = backend._check_verdict
                return SimpleNamespace(output_text=json.dumps(payload), usage=None)

        return SimpleNamespace(responses=_Responses())


def test_run_short_circuits_not_checkable_and_below_floor_claims_without_a_check_call() -> None:
    backend = _DecomposeThenCheckBackend(
        claims=[
            {"text": "the fix is correct in principle", "load_bearing": "high",
             "log_checkable": "no", "evidence_needed": "", "traps": []},
            {"text": "minor formatting cleanup", "load_bearing": "low",
             "log_checkable": "yes", "evidence_needed": "", "traps": []},
            {"text": "ran the full suite and it passed", "load_bearing": "high",
             "log_checkable": "yes", "evidence_needed": "pytest output", "traps": []},
        ],
        check_verdict={"verdict": "confirmed", "evidence": ["0"], "reason": "pytest output shows it"},
    )

    out = run("some report", {"actions": []}, backend, min_load_bearing="medium")

    assert backend.check_calls == 1  # only the third claim clears both gates
    verdicts = [c["verdict"] for c in out.claims]
    assert verdicts == ["not_checkable", "skipped", "confirmed"]
    assert out.claims[0]["reason"] == "No record of tool calls could settle this claim."
    assert out.claims[1]["reason"] == "Below the load-bearing floor."
    assert out.claims[2]["evidence"] == ["0"]


def test_run_on_an_empty_report_makes_no_calls() -> None:
    backend = _DecomposeThenCheckBackend(claims=[], check_verdict={})
    out = run("   ", {"actions": []}, backend)
    assert out.claims == [] and out.requests == 0
