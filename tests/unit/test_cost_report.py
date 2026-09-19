"""eval/cost_report.py is a standalone script, not part of the installed package (it does its own
sys.path insert to reach `receipts`), so it's loaded here by file path rather than imported
normally. Only the pure, network-free helpers are covered: locating sessions/gold files and
Cohen's kappa. The three arms themselves need a live judge backend and are exercised manually
(module docstring), not in CI.
"""
import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "eval" / "cost_report.py"
_spec = importlib.util.spec_from_file_location("cost_report", _PATH)
assert _spec is not None and _spec.loader is not None
cost_report = importlib.util.module_from_spec(_spec)
sys.modules["cost_report"] = cost_report
_spec.loader.exec_module(cost_report)


# ---------- session id / gold file parsing ----------


def test_load_session_ids_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    f = tmp_path / "sessions.txt"
    f.write_text("# header comment\n\nlocal\tabc-123\tcalls=5\tregex_claims=0\n"
                 "local\tdef-456\tcalls=1\tregex_claims=1\n")
    assert cost_report.load_session_ids(str(f)) == ["abc-123", "def-456"]


def test_load_gold_verdicts_returns_none_when_file_absent(tmp_path: Path) -> None:
    assert cost_report.load_gold_verdicts(str(tmp_path / "no-such.csv")) is None


def test_load_gold_verdicts_drops_not_a_claim_rows(tmp_path: Path) -> None:
    f = tmp_path / "reconciled.csv"
    f.write_text("claim_id,label\nc1,confirmed\nc2,not_a_claim\nc3,contradicted\n")
    gold = cost_report.load_gold_verdicts(str(f))
    assert gold == {"c1": "confirmed", "c3": "contradicted"}


def test_find_session_locates_by_id_under_projects_dir(tmp_path: Path) -> None:
    proj = tmp_path / "some-project"
    proj.mkdir()
    (proj / "abc-123.jsonl").write_text("{}\n")
    assert cost_report.find_session("abc-123", str(tmp_path)) == str(proj / "abc-123.jsonl")


def test_find_session_returns_none_when_missing(tmp_path: Path) -> None:
    assert cost_report.find_session("no-such-id", str(tmp_path)) is None


# ---------- Cohen's kappa: agreement, chance, and the pending-gold escape hatch ----------


def test_cohen_kappa_is_one_on_perfect_agreement() -> None:
    labels = ["confirmed", "contradicted", "unwitnessed", "confirmed"]
    assert cost_report.cohen_kappa(labels, labels) == 1.0


def test_cohen_kappa_below_one_on_disagreement() -> None:
    pred = ["confirmed", "confirmed", "confirmed", "confirmed"]
    gold = ["confirmed", "contradicted", "confirmed", "unwitnessed"]
    k = cost_report.cohen_kappa(pred, gold)
    assert k is not None and k < 1.0


def test_cohen_kappa_none_on_mismatched_lengths() -> None:
    assert cost_report.cohen_kappa(["confirmed"], ["confirmed", "contradicted"]) is None


def test_kappa_for_is_none_without_a_gold_file() -> None:
    assert cost_report._kappa_for([], None) is None


def test_kappa_for_is_none_when_no_claims_overlap_the_gold_set() -> None:
    class _Rec:
        def __init__(self, claim_id: str) -> None:
            self.claim_id = claim_id

            class _V:
                value = "confirmed"

            self.verdict = _V()

    assert cost_report._kappa_for([_Rec("not-in-gold")], {"other-claim": "confirmed"}) is None
