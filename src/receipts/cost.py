"""Per-session token and dollar accounting by stage and tier (`receipts cost`).

Records: claims settled per tier, judge requests per session, input/cached/output tokens per
request, and the price-list lookup from config. Produces the chart: judge-everything vs ladder
vs ladder+compressor on the same sessions, with kappa beside each (EVIDENCE_PLAN, Token Company).

Where the price list lives and how it is dated (was NEEDS-DECISION(anush), now resolved):
`~/.receipts/config.toml` under one `[prices.<model-id>]` table per model (`input_per_1m`,
`cached_input_per_1m`, `output_per_1m`), each dated with its own `asof` so a stale figure is
visible in `receipts cost`'s output rather than silently wrong. An unpriced or unknown model
prices at $0.00 rather than raising -- this command must still run before every model in use has
a confirmed price. Real dollar figures are still placeholders (docs/OPEN_QUESTIONS.md); only the
structure is decided here.

Three cost lines, matching the three ways a claim gets settled (verdicts.run, rerun.py, judge.py):
- Tiers 0-2 (`method` "rule"/"state"): zero tokens, zero dollars, by construction.
- Tier 3 (`method` "rerun"): no tokens either -- compute seconds instead, read off the ledger's
  RERUN events' `duration_ms`. There is no fixed $/second to price a local subprocess against, so
  this line is reported in time, never dollars.
- Tier 4 (`method` "judge"): the only place tokens exist. Priced from `judge.Usage`, which is
  metered per session (one batched request), not per claim -- see judge.py's own cost-shape note.

compress.py's bear-2 pass is a fourth, optional line: tokens sent through the compressor before
they reach the judge, priced from the same table under `prices."bear-2"`. It is accounted
separately from judge tokens because it is a real, additional cost paid to save a larger one; the
comparison in eval/cost_report.py is what proves whether that trade is worth it on a given session.

Owner: Anush.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from typing import Any

from rich.table import Table

from . import compress
from .judge import Usage as JudgeUsage
from .models import EventKind, LedgerEvent, VerdictRecord

HOME = os.path.expanduser("~/.receipts")

_TIER_LABEL = {0: "rules", 1: "rules", 2: "rules", 3: "re-run", 4: "judge", 5: "judge"}


@dataclass
class PriceEntry:
    input_per_1m: float = 0.0
    cached_input_per_1m: float = 0.0
    output_per_1m: float = 0.0
    asof: str = ""


PriceTable = dict[str, PriceEntry]


def load_prices(path: str | None = None) -> PriceTable:
    """Read `[prices.<model-id>]` from `~/.receipts/config.toml` (or `path`). No file, no
    `[prices]` table, or no entry for a given model all resolve to an all-zero `PriceEntry` --
    never an exception -- so a session with an unpriced model still gets a receipt, just a
    dollar figure of 0.00 that a reader can tell is unpriced rather than free."""
    p = path or os.path.join(HOME, "config.toml")
    table: PriceTable = {}
    if not os.path.exists(p):
        return table
    with open(p, "rb") as fh:
        data = tomllib.load(fh)
    for model, entry in data.get("prices", {}).items():
        if not isinstance(entry, dict):
            continue
        table[model] = PriceEntry(
            input_per_1m=float(entry.get("input_per_1m", 0.0)),
            cached_input_per_1m=float(entry.get("cached_input_per_1m", 0.0)),
            output_per_1m=float(entry.get("output_per_1m", 0.0)),
            asof=str(entry.get("asof", "")),
        )
    return table


def _price(table: PriceTable, model: str) -> PriceEntry:
    return table.get(model, PriceEntry())


def _dollars(price: PriceEntry, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    return (
        (input_tokens / 1_000_000) * price.input_per_1m
        + (cached_tokens / 1_000_000) * price.cached_input_per_1m
        + (output_tokens / 1_000_000) * price.output_per_1m
    )


@dataclass
class SessionCost:
    """Everything `receipts cost` prints for one session, as both a table and JSON."""
    session_id: str
    claims_total: int = 0
    claims_by_tier: dict[int, int] = field(default_factory=dict)
    claims_by_method: dict[str, int] = field(default_factory=dict)

    rerun_count: int = 0
    rerun_compute_ms: int = 0

    judge_requests: int = 0
    judge_model: str = ""
    judge_input_tokens: int = 0
    judge_cached_input_tokens: int = 0
    judge_output_tokens: int = 0
    judge_dollars: float = 0.0

    compress_enabled: bool = False
    compress_requests: int = 0
    compress_input_tokens: int = 0
    compress_tokens_saved: int = 0
    compress_dollars: float = 0.0

    @property
    def total_dollars(self) -> float:
        return self.judge_dollars + self.compress_dollars

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "claims_total": self.claims_total,
            "claims_by_tier": dict(self.claims_by_tier),
            "claims_by_method": dict(self.claims_by_method),
            "rerun": {"count": self.rerun_count, "compute_ms": self.rerun_compute_ms},
            "judge": {
                "requests": self.judge_requests,
                "model": self.judge_model,
                "input_tokens": self.judge_input_tokens,
                "cached_input_tokens": self.judge_cached_input_tokens,
                "output_tokens": self.judge_output_tokens,
                "dollars": round(self.judge_dollars, 6),
            },
            "compress": {
                "enabled": self.compress_enabled,
                "requests": self.compress_requests,
                "input_tokens": self.compress_input_tokens,
                "tokens_saved": self.compress_tokens_saved,
                "dollars": round(self.compress_dollars, 6),
            },
            "total_dollars": round(self.total_dollars, 6),
        }


def compute(
    session_id: str,
    records: list[VerdictRecord],
    ledger: list[LedgerEvent],
    judge_usage: JudgeUsage | None = None,
    compress_usage: compress.Usage | None = None,
    prices: PriceTable | None = None,
) -> SessionCost:
    """Build a `SessionCost` from the three independent things that already carry this data:
    the settled `VerdictRecord`s (tier/method per claim), the ledger's own RERUN events
    (compute time), and the judge/compressor backends' `Usage` (tokens). Nothing here re-derives
    a number another module already computed."""
    prices = prices if prices is not None else load_prices()
    cost = SessionCost(session_id=session_id, claims_total=len(records))
    for r in records:
        cost.claims_by_tier[r.tier] = cost.claims_by_tier.get(r.tier, 0) + 1
        cost.claims_by_method[r.method] = cost.claims_by_method.get(r.method, 0) + 1

    for e in ledger:
        if e.kind == EventKind.RERUN:
            cost.rerun_count += 1
            cost.rerun_compute_ms += e.duration_ms or 0

    if judge_usage is not None and judge_usage.requests:
        cost.judge_requests = judge_usage.requests
        cost.judge_model = judge_usage.model
        cost.judge_input_tokens = judge_usage.input_tokens
        cost.judge_cached_input_tokens = judge_usage.cached_input_tokens
        cost.judge_output_tokens = judge_usage.output_tokens
        cost.judge_dollars = _dollars(
            _price(prices, judge_usage.model),
            judge_usage.input_tokens, judge_usage.cached_input_tokens, judge_usage.output_tokens,
        )

    if compress_usage is not None and compress_usage.requests:
        cost.compress_enabled = True
        cost.compress_requests = compress_usage.requests
        cost.compress_input_tokens = compress_usage.input_tokens
        cost.compress_tokens_saved = compress_usage.tokens_saved
        cost.compress_dollars = _dollars(_price(prices, "bear-2"), compress_usage.input_tokens, 0, 0)

    return cost


def render_table(cost: SessionCost) -> Table:
    t = Table(show_header=True, header_style="dim", title=f"receipts cost · session {cost.session_id[:8]}…")
    for col in ("stage", "claims", "tokens (in/cached/out)", "compute", "$"):
        t.add_column(col)

    rules_n = sum(n for tier, n in cost.claims_by_tier.items() if _TIER_LABEL.get(tier) == "rules")
    t.add_row("rules (tier 0-2)", str(rules_n), "—", "—", "0.00")

    rerun_n = cost.claims_by_method.get("rerun", 0)
    compute_s = f"{cost.rerun_compute_ms / 1000:.1f}s" if cost.rerun_count else "—"
    t.add_row(f"re-run (tier 3, {cost.rerun_count} run{'s' if cost.rerun_count != 1 else ''})",
              str(rerun_n), "—", compute_s, "0.00")

    judge_n = cost.claims_by_method.get("judge", 0)
    tok = (f"{cost.judge_input_tokens}/{cost.judge_cached_input_tokens}/{cost.judge_output_tokens}"
           if cost.judge_requests else "—")
    t.add_row(f"judge (tier 4{f', {cost.judge_model}' if cost.judge_model else ''})",
              str(judge_n), tok, "—", f"{cost.judge_dollars:.4f}")

    if cost.compress_enabled:
        t.add_row("compress (bear-2, judge window)", "—", f"{cost.compress_input_tokens} in", "—",
                  f"{cost.compress_dollars:.4f}")

    t.add_row("TOTAL", str(cost.claims_total), "", "", f"{cost.total_dollars:.4f}")
    return t
