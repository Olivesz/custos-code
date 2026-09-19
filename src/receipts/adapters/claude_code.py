"""Claude Code session JSONL -> ledger.

Facts established on 779 local transcripts (docs/DESIGN.md §5):
- one JSON object per line; `type` in {user, assistant, system, ...}; assistant/user records carry
  `message.content` as a list of blocks with `type` in {text, tool_use, tool_result}.
- every record has `timestamp`, `sessionId`, `cwd`, `gitBranch`; `isSidechain` marks sub-agent turns.
- tool_use blocks: {id, name, input}; tool_result blocks: {tool_use_id, content (str | list)}.
- the final report is the last assistant `text` block on the main chain.
- transcript_path handed to hooks can lag the file on disk (OPEN_QUESTIONS A5).

Owner: Oliver.
"""
from __future__ import annotations

from ..models import LedgerEvent, Session


def parse(path: str) -> tuple[Session, list[LedgerEvent], str | None]:
    # NEEDS-DECISION(oliver): map Bash exit codes from tool_result text vs toolUseResult field.
    raise NotImplementedError


def find_last_session() -> str:
    """Most recent *.jsonl under ~/.claude/projects (for `receipts check --last`)."""
    raise NotImplementedError
