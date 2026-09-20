"""Mutation score: how many deliberately-broken checkers does our test suite catch?

A pass count says nothing about suite strength — a do-nothing checker passes 9 of our 15
metamorphic relations (docs/GAPS.md G2). Mutation testing answers the real question: if the
checker were subtly wrong, would we notice? We apply small, plausible faults to the modules that
decide verdicts, run the suite against each mutant, and report the fraction killed.

A surviving mutant is a hole in the suite, and its diff says exactly what to test next.

Usage:  .venv/bin/python eval/mutation/run.py [--quick]
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]

# (module, description, pattern, replacement). Each is a fault a careless edit could introduce.
MUTANTS: list[tuple[str, str, str, str]] = [
    # --- verdict-flipping: does the suite notice the wrong answer entirely?
    ("rules.py", "contradicted becomes confirmed on a failing run",
     r'return _rec\(claim, Verdict\.CONTRADICTED, 2, "rule", \[call, res\],\s*\n\s*f"\{parsed\.runner\} at #\{res\.seq\}: \{parsed\.passed\} passed',
     'return _rec(claim, Verdict.CONFIRMED, 2, "rule", [call, res],\n                    f"{parsed.runner} at #{res.seq}: {parsed.passed} passed'),
    ("rules.py", "an empty collection counts as a pass",
     r'if parsed\.collected == 0 and parsed\.passed == 0:',
     'if False:'),
    ("rules.py", "piped output is treated as trustworthy",
     r'if res\.flags\.piped or res\.flags\.truncated:',
     'if False:'),
    ("rules.py", "truncated output is treated as trustworthy",
     r'if res\.flags\.piped or res\.flags\.truncated:',
     'if res.flags.piped:'),
    ("rules.py", "sidechain events count as top-level evidence",
     r'return \[e for e in ledger if not e\.flags\.sidechain\]',
     'return list(ledger)'),
    ("rules.py", "the accusable() guard is disabled",
     r'def accusable\(path: str, state: RepoState\) -> bool:',
     'def accusable(path: str, state: RepoState) -> bool:\n    return True  # MUTANT'),
    ("rules.py", "a bare filename becomes accusable",
     r'if "/" not in path\.strip\("/"\):\s*\n\s*return False',
     'if False:\n        return False'),
    ("rules.py", "missing repo state no longer blocks an accusation",
     r'if not state\.root or not os\.path\.isdir\(state\.root\):\s*\n\s*return False\s*\n\s*if "/" not in',
     'if False:\n        return False\n    if "/" not in'),
    ("rules.py", "a manual check is confirmed instead of unwitnessed",
     r'return _rec\(claim, Verdict\.UNWITNESSED, 1, "rule", \[\], "A manual check leaves no trace',
     'return _rec(claim, Verdict.CONFIRMED, 1, "rule", [], "A manual check leaves no trace'),
    ("rules.py", "a non-zero exit code is ignored",
     r'if res\.exit_code not in \(None, 0\) or res\.flags\.error:\s*\n\s*return _rec\(claim, Verdict\.CONTRADICTED, 2, "rule", \[call, res\], f"\{label\}',
     'if False:\n        return _rec(claim, Verdict.CONTRADICTED, 2, "rule", [call, res], f"{label}'),
    ("rules.py", "an interrupted run is not contradicted",
     r'if res\.flags\.interrupted:', 'if False:'),
    ("rules.py", "a count mismatch is confirmed rather than qualified",
     r'if qual:\s*\n\s*return _rec\(claim, Verdict\.QUALIFIED', 'if False:\n            return _rec(claim, Verdict.QUALIFIED'),
    # --- invariant removal: does the suite notice the safety rails coming off?
    ("verdicts.py", "the judge is allowed to emit contradicted",
     r'if rec\.method == "judge" and rec\.verdict == Verdict\.CONTRADICTED:', 'if False:'),
    ("verdicts.py", "confirmed no longer requires evidence",
     r'if rec\.verdict == Verdict\.CONFIRMED and not rec\.evidence and rec\.method not in \("state", "rerun"\):',
     'if False:'),
    ("verdicts.py", "the judge may overturn a deterministic verdict",
     r'if jrec\.verdict == Verdict\.CONFIRMED:  # the judge may only upgrade unwitnessed',
     'if True:  # MUTANT'),
    ("verdicts.py", "every claim is escalated to the judge",
     r'return rec\.verdict == Verdict\.UNWITNESSED and rec\.tier >= 4', 'return True'),
    # --- extraction: does the suite notice claims being mis-typed or dropped?
    ("claims.py", "the not-a-claim exclusions are disabled",
     r'if _NOT_CLAIM_RE\.search\(sentence\):\s*\n\s*return None', 'if False:\n        return None'),
    # Anchored on the full `_PATH_RE = re.compile(...)` line, not just the string literal: the
    # bare-literal pattern this used to be stopped matching after a ruff-format pass reflowed the
    # surrounding lines, and it silently reported "pattern did not apply" instead of testing
    # anything (docs/GAPS.md G2). Re-verified against the live file with re.subn(count=1) == 1.
    ("claims.py", "absolute paths are truncated again (the 2026-09-19 bug)",
     '_PATH_RE\\ =\\ re\\.compile\\(\\\n\\ \\ \\ \\ r"\\(\\?<!\\[\\\\w/\\~\\.\\-\\]\\)\\(\\(\\?:\\~\\|\\\\\\.\\{1,2\\}\\)\\?/\\?\\(\\?:\\[\\\\w\\.\\-\\]\\+/\\)\\*\\[\\\\w\\.\\-\\]\\+"',
     '_PATH_RE = re.compile(\n    r"(?<![\\\\w/])((?:[\\\\w.-]+/)*[\\\\w.-]+"\n'),
    ("claims.py", "polarity is always 'did' (did_not claims are inverted)",
     r'polarity: Literal\["did", "did_not"\] = "did_not" if ctype == ClaimType\.DID_NOT_TOUCH else "did"',
     'polarity: Literal["did", "did_not"] = "did"'),
    ("judge.py", "judge citations outside the window are accepted",
     r'ev = \[int\(s\) for s in item\.get\("evidence", \[\]\) if int\(s\) in seqs\]',
     'ev = [int(s) for s in item.get("evidence", [])]'),
    ("judge.py", "confirmed without citations is accepted",
     r'verdict = Verdict\.CONFIRMED if item\.get\("verdict"\) == "confirmed" and ev else Verdict\.UNWITNESSED',
     'verdict = Verdict.CONFIRMED if item.get("verdict") == "confirmed" else Verdict.UNWITNESSED'),
    ("judge.py", "a split vote confirms instead of abstaining",
     r'if top == Verdict\.CONFIRMED and n \* 2 <= len\(recs\):', 'if False:'),
    ("judge.py", "the agent's own prose is shown to the judge as evidence",
     r'# TEXT events are the agent\'s own prose: never evidence, so they are not rendered\.',
     'elif e.kind == EventKind.TEXT:\n            lines.append(f"#{e.seq} AGENT_SAID {json.dumps((e.output or \'\')[:400])}")'),
]

TEST_CMD = [".venv/bin/pytest", "-x", "-q", "--no-header", "-p", "no:cacheprovider",
            "tests/unit", "tests/golden", "tests/metamorphic/test_relations.py"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="stop after 8 mutants")
    args = ap.parse_args()
    mutants = MUTANTS[:8] if args.quick else MUTANTS

    killed, survived, invalid = [], [], []
    for module, desc, pat, rep in mutants:
        path = ROOT / "src" / "custos-code" / module
        original = path.read_text()
        mutated, n = re.subn(pat, rep, original, count=1)
        if n == 0:
            invalid.append((module, desc))
            continue
        try:
            path.write_text(mutated)
            t0 = time.time()
            r = subprocess.run(TEST_CMD, cwd=ROOT, capture_output=True, text=True, timeout=600)
            dt = time.time() - t0
            (killed if r.returncode != 0 else survived).append((module, desc, dt))
            print(f"  {'KILLED ' if r.returncode != 0 else 'SURVIVED'}  {module:<12} {desc}")
        finally:
            path.write_text(original)

    total = len(killed) + len(survived)
    print(f"\nmutation score: {len(killed)}/{total} = {len(killed) / total:.0%}" if total else "no valid mutants")
    if survived:
        print("\nSURVIVING MUTANTS — each is a hole in the suite:")
        for module, desc, _ in survived:
            print(f"  - [{module}] {desc}")
    if invalid:
        print("\nPATTERN DID NOT APPLY (mutant needs updating after a refactor):")
        for module, desc in invalid:
            print(f"  - [{module}] {desc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
