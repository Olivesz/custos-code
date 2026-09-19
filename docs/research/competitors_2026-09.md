# Competitor check, 19 Sep 2026

Supplements `tool_landscape_gap.md`. New or previously under-weighted entrants, with the delta that must be stated on the landscape slide.

| Product | Does | Does not | Threat | Source |
|---|---|---|---|---|
| groundtruth (veltiq; forks ogyamada) | Extracts claims from the end-of-turn summary; grades against diff + transcript, deterministic, zero LLM; Verified / Unsupported / Review; agent-neutral (Claude Code native, Codex, Cursor, Gemini, Aider, OpenCode); verify loop with round cap; MIT; ~0 stars | Outcome tier (runner output, exit codes), re-execution, unwitnessed vs unrecorded split, judge tier, benchmark, user evidence, remote/machine classes | Medium on concept; low on execution | github.com/ogyamada/groundtruth |
| Swarm Orchestrator 4.0 (moonrunnerkc) | CI/CD layer that runs Claude Code / Copilot CLI / Codex, parses "tests passing"-style claims, verifies via git diff, build/test exec, file existence; RepairAgent consumes structured failures; ISC; ~1.5k tests | Reading existing sessions (transcript parsing is supplementary, non-blocking); hooks; PR receipts | Low-medium | dev.to/moonrunnerkc; github.com/moonrunnerkc/swarm-orchestrator |
| AgentReceipt (in medscan-ai) | Runs build/tests after an agent run, JSON receipt, gates the next agent | Report parsing | Low; name collision | github.com/drmarktzone-stack/medscan-ai |
| Signed-receipt protocols and vendors (Agent Receipts/Obsigna, Provenant, ProofAgent, AgentMint, Pipelock, veritrail, halo-record, CertNode, Asqav, IETF draft-sahu-agent-action-receipts, ~19 total) | Hash-chained, signed action receipts; tamper evidence; compliance | Claim-vs-log checking of any kind | Low on function; high on naming | github.com/JaredKlopstein/provenant/issues/6 |
| "Trace audit loop" write-up | PASS / UNSUPPORTED / CONTRADICTED over a tool-call fact ledger; claims hand-written as JSON | Code, extraction, feedback | None; validates framing | dev.to/codepro_3283 |

New number: a 2026 study of 23,247 agentic PRs found descriptions claiming never-implemented changes are the most common message-vs-code inconsistency at 45.4% (cited in the groundtruth README; locate the primary paper before quoting on a slide).

## Implications
1. Rename: "Receipts" now denotes signed action receipts in this market and collides with AgentReceipt. Candidate: **Witness** (matches witnessed/unwitnessed vocabulary). See OPEN_QUESTIONS P4.
2. Landscape slide cites groundtruth and Swarm Orchestrator with the specific deltas: outcome + re-run tiers, auto-mode contract (no unverified claim survives; retries must add evidence), verdict split, FalseReportBench with per-model rates, pre-registered study, remote and machine coverage.
3. The moat is evidence, not the concept: the two nearest tools publish no accuracy numbers and have no users.
