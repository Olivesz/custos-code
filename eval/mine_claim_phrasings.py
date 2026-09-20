#!/usr/bin/env python3
"""Mine sentences the regex baseline does *not* flag, bucketed by leading verb/shape, so the
frequent buckets can be turned into high-precision `claims._PATTERNS` additions (issue #27).

Issue #27's plan needs SWE-chat (`SALT-NLP/SWE-chat` on Hugging Face, gated `auto`) for a
representative 6,000-session corpus. Pulling it needs a Hugging Face account that has accepted
the dataset's terms and a token (`HF_TOKEN`) -- neither of which this script can obtain on its
own, and `huggingface_hub` is not in this project's dependencies yet. `download_swechat.py`
alongside this file is the pull step, stubbed and unrun; this script is step 2 (bucket the
misses), written against whatever session source is available so it needs no changes once
SWE-chat is downloaded -- just point --source at the extracted transcripts.

Redacts every sentence before it is written anywhere (invariant 9): local transcripts are the
operator's own, but the buckets file is committed, so treat it the same as any other stored
output.

Usage: uv run python eval/mine_claim_phrasings.py [--source local] [--out eval/results/<date>-claim-phrasing-buckets.md]

Owner: Anush.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from custos_code import claims as claims_mod  # noqa: E402
from custos_code.adapters import claude_code  # noqa: E402
from custos_code.ledger import redact  # noqa: E402

_LEADING_RE = re.compile(r"^\W*(?:I\W*ve|I\W*d|I\W*ll|I|we|we\W*ve|the\s+\w+)?\W*([a-z][a-z'-]*)", re.IGNORECASE)


def leading_verb(sentence: str) -> str:
    """Cheap shape key: the first non-pronoun word, lowercased. Not a parser -- just enough to
    group "Added tests…" with "I added tests…" for a human to skim."""
    m = _LEADING_RE.match(sentence.strip())
    return m.group(1).lower() if m else "?"


def local_sessions(projects_dir: str | None = None) -> list[tuple[str, str]]:
    """(session_id, final_report) for every local Claude Code transcript that has one. This is
    NOT the SWE-chat corpus issue #27 asks for -- it's whatever transcripts already exist on the
    machine running this script, used here only to exercise the bucketing logic end to end."""
    root = projects_dir or os.path.expanduser("~/.claude/projects")
    out: list[tuple[str, str]] = []
    for path in glob.glob(os.path.join(root, "*", "*.jsonl")):
        try:
            sess, _, report = claude_code.parse(path)
        except Exception:  # noqa: BLE001 -- a malformed transcript should not kill the whole run
            continue
        if report and report.strip():
            out.append((sess.id, report))
    return out


@dataclass
class Bucket:
    verb: str
    count: int
    examples: list[str]


def bucket_unflagged(sessions: list[tuple[str, str]]) -> list[Bucket]:
    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    for _, report in sessions:
        for _, sent in claims_mod.sentences(report):
            if claims_mod.classify(sent) is not None:
                continue  # already caught by an existing pattern
            verb = leading_verb(sent)
            counts[verb] += 1
            examples.setdefault(verb, [])
            if len(examples[verb]) < 3:
                examples[verb].append(redact(sent.strip())[:160])
    return sorted(
        (Bucket(verb, n, examples[verb]) for verb, n in counts.items()),
        key=lambda b: b.count, reverse=True,
    )


def render(buckets: list[Bucket], n_sessions: int, source: str) -> str:
    lines = [
        "# Claim-phrasing buckets: sentences the regex baseline does not flag",
        "",
        f"Source: {source} ({n_sessions} session(s) with a final report).",
        "",
        "**Not the SWE-chat corpus issue #27 asks for.** SWE-chat needs a Hugging Face account "
        "that has accepted `SALT-NLP/SWE-chat`'s gated terms plus an `HF_TOKEN`; this run is a "
        "local smoke test of the bucketing tool only, on whatever transcripts already exist on "
        "this machine. Re-run with `--source swechat` (see `download_swechat.py`) once a token "
        "is available, before proposing anything to `claims.py` for real.",
        "",
        "| verb | count | example |",
        "|---|---|---|",
    ]
    for b in buckets[:40]:
        example = (b.examples[0] if b.examples else "").replace("|", "\\|")
        lines.append(f"| {b.verb} | {b.count} | {example} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--source", choices=["local"], default="local",
                     help="Session source. Only 'local' is implemented; SWE-chat needs download_swechat.py first.")
    ap.add_argument("--out", default=None, help="Output markdown path (default: printed to stdout).")
    args = ap.parse_args(argv)

    sessions = local_sessions()
    if not sessions:
        print("no local sessions with a final report found", file=sys.stderr)
        return 1
    buckets = bucket_unflagged(sessions)
    doc = render(buckets, len(sessions), args.source)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(doc)
        print(f"wrote {args.out}")
    else:
        print(doc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
