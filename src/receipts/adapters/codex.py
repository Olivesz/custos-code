"""Codex CLI rollout JSONL. Field mapping unknown until three real rollouts are collected (OPEN_QUESTIONS A1).

Owner: Ananya.
"""
from __future__ import annotations

from ..models import LedgerEvent, Session


def parse(path: str) -> tuple[Session, list[LedgerEvent], str | None]:
    raise NotImplementedError
