"""Optional pre-processor for the judge window only (never the report, never the ledger of
record): calls bear-2 (Token Company, "we only ever delete", <50 ms, cache-safe) to shrink the
rendered ledger window before it reaches the Tier 4 prompt.

Off by default. `make_compressor` returns `None` -- meaning "skip this, use `judge.render_window`
unmodified" -- unless BOTH `[compress].enabled = true` in config AND `RECEIPTS_TTC_API_KEY` is
set. Key presence alone is deliberately not enough: unlike `judge.make_backend`, where a present
key is a reasonable signal to turn the judge itself on, this changes what evidence the judge
*sees*, so it needs an explicit yes in config too.

Scope, kept deliberately narrow: this only ever touches text already produced by
`judge.render_window` from an already-redacted ledger (hooks.py redacts before an event is ever
stored, per invariant 9 in AGENTS.md). It never touches the agent's final report, the ledger of
record, or anything that gets hashed into the chain -- a bug here can waste judge tokens or blur
evidence text the judge reads; it can never put an event into the record the harness did not
write, and it never runs before redaction.

bear-2 is advertised as deterministic ("we only ever delete") and cache-safe, so calling
`compress_window` twice on the same window should return the same text -- that determinism is
what lets it compose with prompt caching rather than fight it (judge.render_window already puts
the ledger before the claims so the request caches; a nondeterministic compressor would defeat
that on every call).

Integration into judge.py itself is NOT done here (docs/OPEN_QUESTIONS.md E11): `Compressor.compress_window`
has the same shape as the module-level `judge.render_window` (`list[LedgerEvent] -> str`), so either
backend's `_once` can swap one for the other in a single line whenever Oliver decides whether
compression should apply once before both backends or per-backend, and whether a compression
failure should fall back to the uncompressed window or abort the judge call. That is his call on
his file, not made here.

Owner: Anush.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from typing import Any

from . import judge
from .models import LedgerEvent

HOME = os.path.expanduser("~/.receipts")


def _config() -> dict[str, Any]:
    cfg: dict[str, Any] = {"enabled": False, "model": "bear-2"}
    p = os.path.join(HOME, "config.toml")
    if os.path.exists(p):
        with open(p, "rb") as fh:
            data = tomllib.load(fh)
        cfg.update(data.get("compress", {}))
    return cfg


@dataclass
class Usage:
    """Token accounting for compressor calls (feeds cost.py's compression line)."""
    requests: int = 0
    input_tokens: int = 0   # best-effort estimate; bear-2's response carries no token count today
    tokens_saved: int = 0

    def add(self, other: Usage) -> None:
        self.requests += other.requests
        self.input_tokens += other.input_tokens
        self.tokens_saved += other.tokens_saved


@dataclass
class Compressor:
    """Wraps one bear-2 client. `compress_window` is the drop-in for `judge.render_window` --
    same input, same output shape -- so wiring it into judge.py's prompt assembly is a one-line
    swap whenever that integration happens."""
    api_key: str
    model: str = "bear-2"
    usage: Usage = field(default_factory=Usage)
    _client: Any = field(default=None, init=False, repr=False)

    def client(self) -> Any:
        if self._client is None:
            # optional dep, not in pyproject.toml (a shared seam) -- this path is off by default
            from thetokencompany import TheTokenCompany  # type: ignore[import-not-found]
            self._client = TheTokenCompany(api_key=self.api_key)
        return self._client

    def compress_window(self, window: list[LedgerEvent]) -> str:
        """Render the window exactly as the judge would see it, then shrink it. Returns the
        unmodified rendered text for an empty window -- nothing to save, no call to make."""
        text = judge.render_window(window)
        if not text:
            return text
        result = self.client().compress(text, model=self.model)
        self.usage.add(Usage(requests=1, input_tokens=len(text) // 4,
                             tokens_saved=int(getattr(result, "tokens_saved", 0) or 0)))
        return str(result.output)


def enabled(cfg: dict[str, Any] | None = None) -> bool:
    cfg = cfg if cfg is not None else _config()
    return bool(cfg.get("enabled", False)) and bool(os.environ.get("RECEIPTS_TTC_API_KEY"))


def make_compressor(cfg: dict[str, Any] | None = None) -> Compressor | None:
    """Config- and key-gated. `None` means "skip compression, use `judge.render_window` as-is" --
    the same shape as `judge.make_backend` returning `None` when no judge key is present, so
    callers can handle both the same way: `c = make_compressor(); text = c.compress_window(w) if
    c else judge.render_window(w)`."""
    cfg = cfg if cfg is not None else _config()
    key = os.environ.get("RECEIPTS_TTC_API_KEY")
    if not enabled(cfg):
        return None
    assert key is not None  # enabled() already checked this
    return Compressor(api_key=key, model=str(cfg.get("model", "bear-2")))
