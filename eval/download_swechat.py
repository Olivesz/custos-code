#!/usr/bin/env python3
"""Pull `SALT-NLP/SWE-chat` from Hugging Face (issue #27, step 0).

Gated dataset: needs a Hugging Face account that has accepted its terms on the dataset page,
then a token with read access, passed as `HF_TOKEN` (never as a CLI argument -- it would end up
in shell history and process listings). `huggingface_hub` is not a project dependency yet since
nothing else needs it; this script is the one place that would use it, added here explicitly
rather than to pyproject.toml's default extras so it stays an opt-in install
(`uv pip install huggingface_hub`).

Not run as part of any CI or test: it downloads ~gigabytes of other people's session transcripts
under ODC-BY. Per the issue: redact on ingest, keep the raw parquet/jsonl out of the repo (add
the target directory to .gitignore if it is not already covered), and commit only derived counts
and patterns -- never the transcripts themselves.

Usage: HF_TOKEN=hf_... uv run python eval/download_swechat.py [--out eval/.swechat]

Owner: Anush.
"""
from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), ".swechat"),
                     help="Local directory to download into (kept out of the repo; see .gitignore).")
    ap.add_argument("--repo-id", default="SALT-NLP/SWE-chat")
    args = ap.parse_args(argv)

    token = os.environ.get("HF_TOKEN")
    if not token:
        print("HF_TOKEN is not set. This dataset is gated: accept its terms at "
              "https://huggingface.co/datasets/SALT-NLP/SWE-chat while logged in, then set "
              "HF_TOKEN to a read token before re-running.", file=sys.stderr)
        return 2
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("huggingface_hub is not installed. `uv pip install huggingface_hub`, then re-run.",
              file=sys.stderr)
        return 2

    path = snapshot_download(repo_id=args.repo_id, repo_type="dataset", token=token, local_dir=args.out)
    print(f"downloaded to {path}")
    print("next: point eval/mine_claim_phrasings.py's local_sessions()-equivalent loader at "
          f"{path}/transcripts/*.jsonl (not implemented here -- the format needs a look once real "
          "files exist), then re-run with --source swechat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
