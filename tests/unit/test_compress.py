from dataclasses import dataclass
from datetime import datetime

import pytest

from custos_code import compress
from custos_code.models import EventKind, LedgerEvent


def _ledger() -> list[LedgerEvent]:
    ts = datetime(2026, 9, 19)
    return [
        LedgerEvent(seq=0, ts=ts, session_id="s", kind=EventKind.CALL, tool="Bash",
                   input={"command": "pytest -q"}),
        LedgerEvent(seq=1, ts=ts, session_id="s", kind=EventKind.RESULT, tool="Bash",
                   output="1 passed", exit_code=0),
    ]


# ---------- gating: config AND key, never key alone ----------


def test_disabled_without_config_flag_even_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_TTC_API_KEY", "ttc-fake")
    assert compress.enabled({"enabled": False}) is False
    assert compress.make_compressor({"enabled": False}) is None


def test_disabled_without_key_even_when_config_enables_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CUSTOS_CODE_TTC_API_KEY", raising=False)
    assert compress.enabled({"enabled": True}) is False
    assert compress.make_compressor({"enabled": True}) is None


def test_enabled_only_when_both_config_and_key_are_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOS_CODE_TTC_API_KEY", "ttc-fake")
    assert compress.enabled({"enabled": True}) is True
    c = compress.make_compressor({"enabled": True, "model": "bear-2"})
    assert c is not None
    assert c.model == "bear-2"
    assert c.api_key == "ttc-fake"


# ---------- compress_window: same shape as judge.render_window, scoped to the window text ----------


@dataclass
class _FakeResult:
    output: str
    tokens_saved: int = 0
    compression_ratio: float = 1.0


class _FakeClient:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def compress(self, text: str, model: str = "bear-2") -> _FakeResult:
        self.calls.append(text)
        return _FakeResult(output="COMPRESSED:" + text[:20], tokens_saved=123)


def test_compress_window_renders_like_the_judge_then_shrinks() -> None:
    calls: list[str] = []
    c = compress.Compressor(api_key="ttc-fake")
    c._client = _FakeClient(calls)  # type: ignore[assignment]

    out = c.compress_window(_ledger())

    assert calls, "the compressor should have been called with the rendered window"
    assert "#0 CALL Bash" in calls[0]  # what the judge would have seen, unmodified going in
    assert out.startswith("COMPRESSED:")
    assert c.usage.requests == 1
    assert c.usage.tokens_saved == 123


def test_compress_window_skips_the_call_on_an_empty_window() -> None:
    calls: list[str] = []
    c = compress.Compressor(api_key="ttc-fake")
    c._client = _FakeClient(calls)  # type: ignore[assignment]

    out = c.compress_window([])

    assert calls == []
    assert out == ""
    assert c.usage.requests == 0


def test_usage_accumulates_across_calls() -> None:
    calls: list[str] = []
    c = compress.Compressor(api_key="ttc-fake")
    c._client = _FakeClient(calls)  # type: ignore[assignment]

    c.compress_window(_ledger())
    c.compress_window(_ledger())

    assert c.usage.requests == 2
    assert c.usage.tokens_saved == 246
