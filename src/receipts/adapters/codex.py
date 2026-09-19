"""Codex rollout JSONL -> ledger (class F; see docs/ADAPTERS.md §3).

File: ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<session_id>.jsonl (CLI and Codex Desktop).
Observed on a real rollout on this machine:
  session_meta{id,cwd,originator,cli_version}               -> Session (key codex:<id>)
  turn_context{turn_id,cwd,sandbox_policy}                   -> turn boundary
  function_call{name=exec_command,arguments{cmd,workdir},call_id}  -> CALL tool=Bash
  exec_command_end{call_id,command[argv],cwd,exit_code,aggregated_output,duration,status,parsed_cmd}
                                                             -> RESULT with argv, exit_code, full output
  function_call_output{call_id,output "... Process exited with code N ... Original token count: T"}
                                                             -> flags.truncated only
  custom_tool_call{name=apply_patch,input "*** Begin Patch"}  -> CALL tool=Edit, paths from Add/Update/Delete File
  patch_apply_end{call_id,success,changes{path:{type,content}}} -> RESULT per path
  task_complete{turn_id,last_agent_message}                  -> TEXT (the report for the turn)
  turn_aborted / compacted / thread_rolled_back              -> integrity flags

Owner: Ananya.
"""
from __future__ import annotations

from ..models import LedgerEvent, Session


def parse(path: str) -> tuple[Session, list[LedgerEvent], str | None]:
    raise NotImplementedError


def find_last_session() -> str:
    """Most recent rollout under ~/.codex/sessions."""
    raise NotImplementedError
