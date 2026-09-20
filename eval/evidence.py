"""The other half: what the agent actually ran, as an artifact a claim can be checked against.

`review.annotate` renders the ledger for a model. This renders it for pairing -- deduplicated,
grouped by what the command was for, and carrying the outcome rather than the raw output, so a
2,500-event session becomes something a person or a critic can hold next to a list of claims.

Emits JSON by default so it lines up with the claim decomposition.

Run:  .venv/bin/python eval/evidence.py --transcript PATH [--text]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from custos_code import parsers
from custos_code.adapters import claude_code  # noqa: E402
from custos_code.ledger import MAX_OUTPUT_BYTES  # noqa: E402
from custos_code.models import EventKind  # noqa: E402

# What a command was for. Order matters: first match wins.
KINDS: list[tuple[str, re.Pattern[str]]] = [
    ("test", re.compile(r"\b(pytest|jest|vitest|go test|cargo test|npm test|unittest)\b")),
    ("lint", re.compile(r"\b(ruff|mypy|eslint|tsc|flake8|black|clippy)\b")),
    ("build", re.compile(r"\b(make|cargo build|go build|npm run build|tsc -b|docker build)\b")),
    ("vcs", re.compile(r"^\s*git\b|^\s*gh\b")),
    ("install", re.compile(r"\b(pip install|uv pip|npm i|npm install|poetry add|cargo add)\b")),
    ("network", re.compile(r"\b(curl|wget|https?://)")),
    ("read", re.compile(r"^\s*(cat|head|tail|less|grep|rg|find|ls|sed -n|wc)\b")),
]


def _kind(tool: str, cmd: str) -> str:
    if tool in ("Write", "Edit", "NotebookEdit"):
        return "write"
    if tool in ("Read", "Grep", "Glob"):
        return "read"
    if tool != "Bash":
        return tool.lower()
    for name, rx in KINDS:
        if rx.search(cmd):
            return name
    return "other"


def _outcome(res: object, cmd: str) -> dict[str, object]:
    """What the run actually produced -- the part a claim can be checked against."""
    if res is None:
        return {"outcome": "no result recorded"}
    out = getattr(res, "output", "") or ""
    rc = getattr(res, "exit_code", None)
    flags = [k for k, v in res.flags.model_dump().items() if v]
    parsed = parsers.parse(out, rc)
    d: dict[str, object] = {}
    if parsed:
        d["runner"] = parsed.runner
        d["passed"], d["failed"] = parsed.passed, parsed.failed
        if parsed.collected is not None:
            d["collected"] = parsed.collected
        if parsed.skipped:
            d["skipped"] = parsed.skipped
    if rc is not None:
        d["exit_code"] = rc
    if flags:
        d["flags"] = flags
    if parsers.is_piped(cmd):
        d["output_filtered"] = True
    # The whole retained output, not a summary of it. Showing only the last line made a careful
    # reader conclude three verifiable claims were unverifiable: a `cat -n` of the source file and
    # a pytest failure diff containing the exact literal a claim quoted were both in the ledger,
    # and both rendered as one unrelated trailing line. A renderer that decides what the recorder
    # kept is the same defect this project exists to catch, committed by the tool doing the
    # catching. `output_filtered` still says the agent's own pipe may have cut it upstream.
    d["output"] = out[:MAX_OUTPUT_BYTES]
    d["output_lines"] = len(out.splitlines())
    d["outcome"] = (out.strip().splitlines() or ["(no output)"])[-1][:140]
    return d


def collect(path: str, since_last_user: bool = False,
            include_sidechain: bool = False) -> dict[str, object]:
    """`since_last_user` keeps only events after the last USER event -- one turn's work.

    That is the unit a reply is accountable for: everything the agent did between being asked and
    answering. Anything earlier belongs to a claim it already made.
    """
    _s, ledger, report = claude_code.parse(path)
    if since_last_user:
        last = max((i for i, e in enumerate(ledger) if e.kind is EventKind.USER), default=-1)
        ledger = ledger[last + 1:]
    byseq = {e.seq: e for e in ledger}
    seen: dict[str, dict[str, object]] = {}
    order: list[str] = []

    for e in ledger:
        # A sub-agent's work is not the parent's evidence, so it is skipped by default. But when
        # the transcript IS the sub-agent's own, every event is flagged sidechain and skipping
        # them yields an empty record of a session that did real work.
        if e.kind is not EventKind.CALL or (e.flags.sidechain and not include_sidechain):
            continue
        cmd = str((e.input or {}).get("command") or (e.input or {}).get("file_path") or "")
        if not cmd:
            continue
        key = hashlib.sha1(f"{e.tool}\x00{cmd}".encode()).hexdigest()[:10]
        res = byseq.get(e.seq + 1)
        res = res if res is not None and res.kind is EventKind.RESULT else None
        if key in seen:
            entry = seen[key]
            entry["times_run"] = int(entry.get("times_run", 1)) + 1
            entry["seqs"] = [*list(entry.get("seqs", [])), e.seq]  # type: ignore[list-item]
            continue
        entry = {"id": key, "kind": _kind(e.tool, cmd), "tool": e.tool,
                 "command": cmd[:300], "times_run": 1, "seqs": [e.seq],
                 **_outcome(res, cmd)}
        seen[key] = entry
        order.append(key)

    items = [seen[k] for k in order]
    by_kind: dict[str, int] = {}
    for it in items:
        by_kind[str(it["kind"])] = by_kind.get(str(it["kind"]), 0) + 1
    return {
        "transcript": path,
        "events": len(ledger),
        "distinct_actions": len(items),
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        "report_chars": len(report or ""),
        "actions": items,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--kind", default="", help="only this kind (test, lint, vcs, write, ...)")
    ap.add_argument("--since-last-user", action="store_true",
                    help="only this turn: events after the last user message")
    ap.add_argument("--include-sidechain", action="store_true",
                    help="the transcript is a sub-agent's own, so its events are all sidechain")
    ap.add_argument("--text", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    data = collect(args.transcript, args.since_last_user, args.include_sidechain)
    if args.kind:
        data["actions"] = [a for a in data["actions"] if a["kind"] == args.kind]  # type: ignore[union-attr]

    if args.text:
        lines = [f"{data['events']} events -> {data['distinct_actions']} distinct actions",
                 f"by kind: {data['by_kind']}", ""]
        for a in data["actions"]:  # type: ignore[union-attr]
            n = f" x{a['times_run']}" if a["times_run"] > 1 else ""
            lines.append(f"[{a['kind']:8}]{n:5} {a['command'][:96]}")
            lines.append(f"           -> {a.get('outcome', '')[:96]}")
        text = "\n".join(lines)
    else:
        text = json.dumps(data, indent=2)

    if args.out:
        pathlib.Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(text):,} chars)")
    else:
        print(text[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
