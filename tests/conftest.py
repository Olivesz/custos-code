"""Hermetic test environment.

Several unit tests assert on the *deterministic rules* path, which `make_backend()` selects only
when no API key is available. That was an accident of the developer's shell, not something the
tests stated: anyone with OPENAI_API_KEY exported got the judge path instead and saw failures that
looked like real regressions. Once `make_backend()` started reading ~/.custos-code/env (so the CLI
and the hooks agree about where a key lives), it became true on every machine that has the file.

So state it explicitly and make it true everywhere: no keys, no env file, for the whole suite.
This also stops the tests from making real, billed network calls if someone's key is exported,
and it propagates to subprocesses, which the hook tests spawn.

A test that genuinely wants a backend should opt in by setting the variables itself.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_model_backend(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CUSTOS_CODE_JUDGE_BACKEND"):
        monkeypatch.delenv(var, raising=False)
    missing = tmp_path_factory.getbasetemp() / "no-such-env-file"
    monkeypatch.setenv("CUSTOS_CODE_ENV_FILE", str(missing))
