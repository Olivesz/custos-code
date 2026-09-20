"""Exercise the public review and Stop paths, not just the corroboration helper."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from custos_code import hooks, rerun, review
from custos_code.models import LedgerEvent


@pytest.mark.parametrize("model_verdict,failed,expected", [
    ("contradicted", False, None),
    ("contradicted", True, "block"),
    # A model-only accusation is downgraded to `unrecorded` by _corroborate, and `unrecorded`
    # is not in the default clear-set, so it must NOT hold the turn. It still appears in the
    # receipt. Blocking on it is how an ungrounded opinion became a four-minute, three-pass stall
    # on an honest report -- measured at 21.0% of all real claims on 2026-09-20.
    ("unrecorded", False, None),
])
def test_auto_mode_preserves_real_blocks_but_not_model_only_accusations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    model_verdict: str, failed: bool, expected: str | None,
) -> None:
    monkeypatch.setattr(hooks, "HOME", str(tmp_path / "hooks"))
    monkeypatch.setattr(rerun, "_sessions_root", lambda: tmp_path / "sessions")
    monkeypatch.delenv("CUSTOS_CODE_ONLY_IN", raising=False)
    monkeypatch.setenv("CUSTOS_CODE_AUTO", "1")
    response = SimpleNamespace(output_text=json.dumps({"claims": [{
        "claim": "All tests pass.", "verdict": model_verdict,
        "evidence": [1], "reason": "model's reasoning",
    }]}), usage=None)
    backend = SimpleNamespace(client=lambda: SimpleNamespace(
        responses=SimpleNamespace(create=lambda **kw: response)))
    monkeypatch.setattr(hooks.judge_mod, "make_backend", lambda: backend)
    live, _, receipt = hooks._paths("s")
    rows = [
        LedgerEvent(seq=0, ts="2026-09-20T00:00:00Z", session_id="s", kind="call",
                    tool="Bash", input={"command": "pytest"}),
        LedgerEvent(seq=1, ts="2026-09-20T00:00:00Z", session_id="s", kind="result",
                    tool="Bash", exit_code=int(failed),
                    output="collected 2 items\n" + ("1 failed, 1 passed" if failed else "2 passed")),
    ]
    Path(live).write_text("".join(e.model_dump_json() + "\n" for e in rows))
    result = hooks.on_stop({"session_id": "s", "cwd": str(tmp_path),
                            "last_assistant_message": "All tests pass."})
    assert (result["decision"] if result else None) == expected
    text = Path(receipt).read_text()
    downgraded = model_verdict == "contradicted" and not failed
    if downgraded:
        assert review.MODEL_ONLY_QUALIFIER in text
        assert "model's reasoning" in text
    elif expected is None:
        # Not a downgrade: the model said `unrecorded` itself. It is reported, never gated.
        assert review.MODEL_ONLY_QUALIFIER not in text
        assert "All tests pass." in text
    elif failed:
        assert "tier 2 · rule" in text
