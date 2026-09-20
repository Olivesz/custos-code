"""Print fixtures as a human can check them: the tool calls, the output, the claims, the answer.

`--traps` prints only the fixtures with a non-`confirmed` claim. Those 29 claims out of 233 are the
only ones that discriminate: 204 are honest controls, so an arm that answers `confirmed` to
everything scores 87.6% without checking anything.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from custos_code.adapters import claude_code  # noqa: E402
from custos_code.models import EventKind  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
TRUTH = json.loads((ROOT / "eval" / "arms" / "truth.json").read_text())
PLAIN = {"confirmed": "TRUE  - the log shows it happened",
         "contradicted": "FALSE - the log shows it did not, or that it failed",
         "qualified": "TRUE BUT NARROWER than the claim implies",
         "unrecorded": "UNKNOWN - a tool ran but its outcome was not captured",
         "unwitnessed": "UNKNOWN - nothing in the log either way"}


def dump(name: str, out: list[str]) -> None:
    fx = ROOT / "eval" / "arms" / "fixtures" / f"{name}.jsonl"
    if not fx.exists():
        return
    _s, ledger, report = claude_code.parse(str(fx))
    wants = TRUTH.get(name) or []
    out.append(f"\n## `{name}`  ({wants[0]['family'] if wants else '?'})\n")
    out.append("### What the agent actually did\n")
    for e in ledger:
        if e.kind is EventKind.CALL:
            v = (e.input or {}).get("command") or (e.input or {}).get("file_path") or ""
            out.append(f"    #{e.seq}  $ {v}")
        elif e.kind is EventKind.RESULT:
            body = (e.output or "").rstrip() or "(no output)"
            flags = [k for k, x in e.flags.model_dump().items() if x]
            head = f"    #{e.seq}  -> " + (f"[{','.join(flags)}] " if flags else "")
            out.append(head + body.replace("\n", "\n         "))
    out.append("\n### What the agent reported\n")
    out.append("".join(f"    {ln}\n" for ln in (report or "").splitlines()))
    out.append("### The claims, and the answer\n")
    out.append("| claim | truth | why |")
    out.append("|---|---|---|")
    for w in wants:
        out.append(f"| {w['claim']} | **{PLAIN.get(w['verdict'], w['verdict'])}** | {w['why']} |")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--traps", action="store_true")
    ap.add_argument("--honest", type=int, default=3)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    traps = [n for n, v in TRUTH.items() if any(c["verdict"] != "confirmed" for c in v)]
    honest = [n for n, v in TRUTH.items() if all(c["verdict"] == "confirmed" for c in v)]
    names = sorted(traps) + ([] if args.traps else sorted(honest)[: args.honest])

    out = [
        "# The test set, in full",
        "",
        "**These fixtures are synthetic.** `eval/arms/generate.py` builds each one so the correct",
        "answer follows from the construction rather than from anyone's judgement. They are not",
        "captured from real sessions.",
        "",
        f"- {len(TRUTH)} fixtures, {sum(len(v) for v in TRUTH.values())} claims",
        f"- **{sum(1 for v in TRUTH.values() for c in v if c['verdict'] == 'confirmed')} of them are honest**",
        "  (the claim is true and must NOT be flagged)",
        f"- only **{sum(1 for v in TRUTH.values() for c in v if c['verdict'] != 'confirmed')}** are traps",
        "",
        "That balance is the problem with every accuracy number in this repo: answering",
        "`confirmed` to all 233 claims scores **87.6%**. Our shipped pipeline scores 88.2%.",
        "The two are not distinguishable by that metric, so it should not be quoted.",
        "",
        "Below: every trap fixture, then a few honest controls.",
    ]
    for n in names:
        dump(n, out)
    text = "\n".join(out) + "\n"
    if args.out:
        pathlib.Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(text):,} chars, {len(names)} fixtures)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
