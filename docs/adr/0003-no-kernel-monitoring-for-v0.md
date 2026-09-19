# No eBPF/kernel monitoring in v0

**Status:** accepted (2026-09-19)

**Context and decision.** The harness-written transcript is model-independent, which is enough for the event. It is not harness-independent (harness bugs can log phantom success), so edit/create claims also require filesystem or git agreement. eBPF is the escalation path (AgentSight, agent-trace).

**Consequences.** See AGENTS.md invariants and docs/DESIGN.md §16.
