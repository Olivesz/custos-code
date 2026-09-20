from datetime import datetime
from pathlib import Path
from typing import Literal

from custos_code import compress
from custos_code.cost import PriceEntry, compute, load_prices
from custos_code.judge import Usage as JudgeUsage
from custos_code.models import EventFlags, EventKind, LedgerEvent, Verdict, VerdictRecord

Method = Literal["rule", "rerun", "judge", "state"]


def _rec(cid: str, tier: int, method: Method) -> VerdictRecord:
    return VerdictRecord(claim_id=cid, verdict=Verdict.CONFIRMED, tier=tier, method=method,
                          confidence=0.9, evidence=[0])


def _rerun_event(seq: int, duration_ms: int) -> LedgerEvent:
    return LedgerEvent(seq=seq, ts=datetime(2026, 9, 19), session_id="s", kind=EventKind.RERUN,
                       tool="rerun_tests", duration_ms=duration_ms, flags=EventFlags())


# ---------- load_prices: file location, dating, graceful absence ----------


def test_load_prices_missing_file_returns_empty_table(tmp_path: Path) -> None:
    assert load_prices(str(tmp_path / "no-such-config.toml")) == {}


def test_load_prices_reads_per_model_tables_with_asof(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[prices."gpt-5.2"]\nasof = "2026-09-19"\ninput_per_1m = 2.0\ncached_input_per_1m = 0.5\noutput_per_1m = 8.0\n'
    )
    table = load_prices(str(cfg))
    assert table["gpt-5.2"].input_per_1m == 2.0
    assert table["gpt-5.2"].cached_input_per_1m == 0.5
    assert table["gpt-5.2"].output_per_1m == 8.0
    assert table["gpt-5.2"].asof == "2026-09-19"


def test_unpriced_model_prices_at_zero_not_an_exception() -> None:
    cost = compute("s", [_rec("c1", 4, "judge")], [],
                    judge_usage=JudgeUsage(requests=1, input_tokens=1000, output_tokens=500, model="unknown-model"),
                    prices={})
    assert cost.judge_dollars == 0.0
    assert cost.judge_requests == 1  # the request still gets counted; only the price is zero


# ---------- compute: three independent lines, rules / rerun / judge ----------


def test_rules_tiers_cost_zero_by_construction() -> None:
    records = [_rec("c1", 0, "rule"), _rec("c2", 1, "state"), _rec("c3", 2, "rule")]
    cost = compute("s", records, [])
    assert cost.claims_by_tier == {0: 1, 1: 1, 2: 1}
    assert cost.judge_dollars == 0.0
    assert cost.judge_requests == 0


def test_rerun_line_is_compute_time_not_tokens_or_dollars() -> None:
    ledger = [_rerun_event(0, 1500), _rerun_event(1, 500)]
    cost = compute("s", [_rec("c1", 3, "rerun")], ledger)
    assert cost.rerun_count == 2
    assert cost.rerun_compute_ms == 2000
    assert cost.claims_by_method["rerun"] == 1


def test_judge_line_prices_tokens_from_the_table() -> None:
    table = {"gpt-5.2": PriceEntry(input_per_1m=2.0, cached_input_per_1m=0.5, output_per_1m=8.0)}
    usage = JudgeUsage(requests=1, input_tokens=1_000_000, cached_input_tokens=1_000_000,
                       output_tokens=1_000_000, model="gpt-5.2")
    cost = compute("s", [_rec("c1", 4, "judge")], [], judge_usage=usage, prices=table)
    assert cost.judge_dollars == 2.0 + 0.5 + 8.0
    assert cost.judge_model == "gpt-5.2"


def test_no_judge_usage_means_zero_judge_cost() -> None:
    cost = compute("s", [_rec("c1", 1, "rule")], [])
    assert cost.judge_requests == 0
    assert cost.judge_dollars == 0.0


# ---------- compress line: additional, separate from judge, off unless usage is passed ----------


def test_compress_usage_absent_leaves_compress_disabled() -> None:
    cost = compute("s", [], [])
    assert cost.compress_enabled is False
    assert cost.compress_dollars == 0.0


def test_compress_usage_present_prices_from_the_bear_2_entry() -> None:
    table = {"bear-2": PriceEntry(input_per_1m=0.05)}
    usage = compress.Usage(requests=1, input_tokens=1_000_000, tokens_saved=400_000)
    cost = compute("s", [], [], compress_usage=usage, prices=table)
    assert cost.compress_enabled is True
    assert cost.compress_dollars == 0.05
    assert cost.compress_tokens_saved == 400_000


def test_to_dict_is_json_serializable_round_trip() -> None:
    import json

    cost = compute("session-abc", [_rec("c1", 4, "judge")], [_rerun_event(0, 100)],
                    judge_usage=JudgeUsage(requests=1, input_tokens=10, output_tokens=5, model="m"))
    dumped = json.dumps(cost.to_dict())
    assert json.loads(dumped)["session_id"] == "session-abc"
