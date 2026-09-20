"""Shared types. Every other module imports from here; nothing here imports from them.

Mirrors docs/DESIGN.md §9. If you change a field, update the design doc and the golden tests.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class EventKind(StrEnum):
    CALL = "call"  # a tool invocation, written by the harness
    RESULT = "result"  # the tool's output, written by the harness
    TEXT = "text"  # assistant prose (never evidence)
    USER = "user"  # user message
    META = "meta"  # session metadata
    RERUN = "rerun"  # a Tier 3 re-execution; itself auditable


class EventFlags(BaseModel):
    truncated: bool = False  # output cut at max_output_bytes; full hash kept
    piped: bool = False  # command contained | head, | tail, 2>/dev/null, etc.
    stderr_dropped: bool = False
    sidechain: bool = False  # sub-agent; never counts as top-level evidence
    error: bool = (
        False  # harness marked the result as an error (Claude Code is_error; Codex success=false)
    )
    interrupted: bool = False  # tool run was interrupted
    timed_out: bool = False  # Tier 3 re-run hit its timeout_s budget before the command finished


class LedgerEvent(BaseModel):
    """One harness-written fact. The model has no write path to this.

    Invariant 1 (AGENTS.md): only adapters and hooks construct these.
    """

    seq: int
    ts: datetime
    session_id: str
    kind: EventKind
    tool: str | None = None
    input: dict[str, object] | None = None  # redacted before hashing
    output: str | None = None  # <= max_output_bytes
    output_hash: str | None = None  # sha256 of the full, untruncated output
    exit_code: int | None = None
    paths: list[str] = Field(default_factory=list)
    cwd: str | None = None
    duration_ms: int | None = None
    flags: EventFlags = Field(default_factory=EventFlags)
    prev_hash: str = ""
    hash: str = ""  # sha256(prev_hash + canonical_json(self without hash))


class ClaimType(StrEnum):
    EDIT = "edit"
    CREATE = "create"
    DELETE = "delete"
    READ = "read"
    RUN_CMD = "run_cmd"
    RUN_TESTS = "run_tests"
    BUILD = "build"
    VERIFY = "verify"
    DEPLOY = "deploy"
    COMMIT = "commit"
    REVIEW_ALL = "review_all"
    DID_NOT_TOUCH = "did_not_touch"
    OBSERVED_OUTPUT = "observed_output"
    DESIGN_PROPERTY = "design_property"
    OTHER = "other"


class Claim(BaseModel):
    id: str
    session_id: str
    text: str  # verbatim span from the report
    type: ClaimType
    objects: list[str] = Field(default_factory=list)  # paths, commands, test names, URLs
    polarity: Literal["did", "did_not"] = "did"
    source: Literal["report", "plan", "request"] = "report"


class Verdict(StrEnum):
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"  # positive evidence only; never from the judge (invariant 2, 3)
    UNWITNESSED = "unwitnessed"
    UNRECORDED = "unrecorded"
    QUALIFIED = "qualified"


class VerdictRecord(BaseModel):
    claim_id: str
    verdict: Verdict
    tier: int = Field(ge=0, le=5)
    method: Literal["rule", "rerun", "judge", "state"]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[int] = Field(
        default_factory=list
    )  # ledger seq numbers; required unless unwitnessed/unrecorded
    rationale: str = ""  # one sentence
    qualifier: str | None = None  # for QUALIFIED: what changed under the claim


class Coverage(BaseModel):
    requirement: str
    claim_ids: list[str] = Field(default_factory=list)
    hunks: list[str] = Field(default_factory=list)
    status: Literal["done", "unclaimed", "unrequested", "needs_human"]


class Session(BaseModel):
    id: str
    source: str  # adapter name, e.g. "claude_code"/"codex"/"copilot"/"devin"/"machine"/"otel" (ADR 0006):
                 # open, not a closed Literal, so a new adapter never has to touch this shared-seam file
    agent: str
    model: str | None = None
    started: datetime | None = None
    ended: datetime | None = None
    cwd: str | None = None
    git_branch: str | None = None
    n_events: int = 0
    ledger_root_hash: str = ""
    integrity_score: float = 1.0
