#!/usr/bin/env python3
"""Offline scope evidence, not a false-positive oracle or a production gate.

No recorded command is executed. Use frozen local transcript directories for repeatable runs.
Only aggregate counts leave this tool by default. --review-local writes redacted action/context
text locally for inspection; never commit that file. Unknown tools and missing inputs are not
GREEN evidence. State checks describe the current filesystem, not historical recoverability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from custos_code import scope  # noqa: E402
from custos_code.adapters import claude_code, codex  # noqa: E402
from custos_code.ledger import redact  # noqa: E402
from custos_code.models import EventKind, LedgerEvent  # noqa: E402
from custos_code.rules import RepoState  # noqa: E402

READS = frozenset({"Read", "Glob", "Grep", "NotebookRead", "WebFetch", "WebSearch", "TodoWrite"})
WRITES = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "str_replace_based_edit_tool"})


def exclusion(event: LedgerEvent) -> str | None:
    inp = event.input or {}
    if event.tool == "Bash":
        return None if isinstance(inp.get("command"), str) and inp["command"].strip() else "missing-command"
    if event.tool in WRITES:
        return None if any(isinstance(inp.get(k), str) and inp[k] for k in
                           ("file_path", "path", "notebook_path")) else "missing-write-path"
    return None if event.tool in READS else "unsupported-tool"


def fingerprint(event: LedgerEvent) -> str:
    # Preserve repeated actions at different times, but remove copied transcript history.
    # Cwd is checked separately: disagreeing copies are excluded, not arbitrarily resolved.
    blob = [event.ts.isoformat(), event.tool, event.input]
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()


def evaluate(roots: dict[str, Path]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    summary: dict[str, Any] = {"sources": {}}
    review: list[dict[str, Any]] = []
    states: dict[str, RepoState] = {}
    seen: dict[tuple[str, str], str | None] = {}
    for source, root in roots.items():
        adapter = {"claude_code": claude_code, "codex": codex}[source]
        counts: Counter[str] = Counter()
        bands: Counter[str] = Counter()
        rules: Counter[str] = Counter()
        excluded: Counter[str] = Counter()
        sessions: list[dict[str, Any]] = []
        parsed = []
        contexts: dict[str, set[str | None]] = {}
        for index, path in enumerate(sorted(root.rglob("*.jsonl"))):
            counts["files"] += 1
            try:
                session, events, _ = adapter.parse(str(path))
            except Exception:  # noqa: BLE001 -- account for failures without leaking transcript text
                counts["parse_errors"] += 1
                continue
            counts["parsed_files"] += 1
            parsed.append((index, path, session, events))
            for event in events:
                if event.kind is EventKind.CALL:
                    contexts.setdefault(fingerprint(event), set()).add(event.cwd or session.cwd)
        for index, path, session, events in parsed:
            calls = [e for e in events if e.kind is EventKind.CALL]
            counts["parsed_calls"] += len(calls)
            if not calls:
                counts["files_without_calls"] += 1
            local: Counter[str] = Counter()
            users: list[str] = []
            recent: list[str] = []
            for event in events:
                if event.kind is EventKind.USER:
                    users.append(event.output or "")
                elif event.kind is EventKind.TEXT:
                    recent = (recent + [event.output or ""])[-2:]
                if event.kind is not EventKind.CALL:
                    continue
                key = (source, fingerprint(event))
                if key in seen:
                    counts["duplicate_calls"] += 1
                    if seen[key] != event.cwd:
                        counts["duplicate_cwd_disagreements"] += 1
                    continue
                seen[key] = event.cwd
                counts["unique_calls"] += 1
                reason = ("ambiguous-cwd" if len(contexts[fingerprint(event)]) > 1
                          else exclusion(event))
                cwd = event.cwd or session.cwd
                if reason or not cwd:
                    excluded[reason or "missing-cwd"] += 1
                    continue
                assert cwd is not None
                if cwd not in states:
                    states[cwd] = RepoState(cwd)
                finding = scope.classify(event.tool or "", event.input or {},
                                         scope.Grant.for_session(cwd), states[cwd])
                counts["evaluated_calls"] += 1
                local[finding.band.value] += 1
                bands[finding.band.value] += 1
                if finding.gates:
                    rules[finding.rule] += 1
                    review.append({"case": f"case-{len(review) + 1:03d}", "source": source,
                                   "file_index": index, "seq": event.seq, "band": finding.band.value,
                                   "rule": finding.rule, "input": event.input, "cwd": cwd,
                                   "users_before": users.copy(), "assistant_before": recent.copy(),
                                   "transcript": str(path), "timestamp": event.ts.isoformat()})
            if local:
                counts["files_with_evaluated_calls"] += 1
                if local["red"] or local["yellow"]:
                    counts["files_with_flags"] += 1
                sessions.append(dict(local))
        total = counts["evaluated_calls"]
        flags = bands["red"] + bands["yellow"]
        for key in ("files", "parsed_files", "parse_errors", "parsed_calls", "unique_calls",
                    "evaluated_calls", "duplicate_calls", "duplicate_cwd_disagreements",
                    "files_without_calls", "files_with_evaluated_calls", "files_with_flags"):
            counts.setdefault(key, 0)
        summary["sources"][source] = {
            "counts": dict(counts), "bands": dict(bands), "rules": dict(rules),
            "excluded_calls": dict(excluded), "per_file_bands": sessions,
            "flag_rate": flags / total if total else None,
            "flagged_file_rate": (counts["files_with_flags"] / counts["files_with_evaluated_calls"]
                                  if counts["files_with_evaluated_calls"] else None),
        }
    return summary, review


def enrich_review(rows: list[dict[str, Any]]) -> None:
    """Add raw string-form user messages omitted by the current Claude adapter, locally only.

    These are context for a reviewer, not machine-interpreted authorization. They may include
    compaction summaries or forwarded messages; those must not be treated as user approvals.
    """
    cache: dict[str, list[tuple[datetime, str]]] = {}
    for row in rows:
        path = row["transcript"]
        if path not in cache:
            cache[path] = []
            for line in Path(path).read_text().splitlines():
                try:
                    obj = json.loads(line)
                    message = obj.get("message", {})
                    text = message.get("content") if isinstance(message, dict) else None
                    if obj.get("type") == "user" and isinstance(text, str):
                        ts = datetime.fromisoformat(obj["timestamp"].replace("Z", "+00:00"))
                        cache[path].append((ts, str(redact(text))))
                except (ValueError, KeyError, AttributeError, TypeError):
                    continue
        cutoff = datetime.fromisoformat(row["timestamp"])
        row["raw_user_context_before"] = [text for ts, text in cache[path] if ts <= cutoff]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude-root", type=Path, required=True)
    parser.add_argument("--codex-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--review-local", type=Path)
    args = parser.parse_args()
    for root in (args.claude_root, args.codex_root):
        if not root.is_dir():
            parser.error("transcript roots must be existing directories")
    result, review = evaluate({"claude_code": args.claude_root, "codex": args.codex_root})
    result["code_commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1], text=True,
    ).strip()
    result["instrument_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["method"] = "offline, default grant, current repository state, no approval replay"
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    if args.review_local:
        enrich_review(review)
        # Exclusive creation avoids overwriting a file/symlink or reusing permissive modes.
        fd = os.open(args.review_local, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(review, stream, indent=2)
            stream.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
