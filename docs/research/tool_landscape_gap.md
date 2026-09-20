# Tool and research landscape: does anything check an agent's natural-language final report against an independent action log, with no per-task spec?

Scope note: as of 2026-09-19. Vocabulary used throughout, per the assignment's constraint:
- **logs it** = records the events (tool calls, commands, file edits, LLM I/O) somewhere;
- **shows it to a human** = renders that record for manual review (timeline, replay, transcript);
- **checks the report against it** = machine compares the agent's *own account* (natural-language final message / summary) to the record and emits a verdict.
Per-entry fields requested: records / checks / parses self-report? / needs per-task spec? / pricing-openness / verdict.

---

## Key question 1: Observability platforms — do any offer "claim verification" / "self-report accuracy" / "did the agent do what it said"? What do their agent evaluators actually score?

### Takeaway
None of the eleven platforms ships an evaluator that parses the agent's final natural-language report and checks it against the logged tool calls. Their agent-specific evaluators score tool *selection*, tool *argument* correctness, trajectory match against a *reference*, goal completion against the *user's request*, or path efficiency; the only "faithfulness"-shaped scorers are RAG-style (output vs. a supplied `context` column) and would have to be repurposed by hand. Langfuse's evaluator model actively cannot see child tool spans unless the app copies them up to the root observation.

### Cited Findings

**Braintrust**
- Records: traces; scorers can be "built-in autoevals, LLM-as-a-judge, or custom code"; the page lists no scorer names — [Braintrust docs: Evaluate](https://www.braintrust.dev/docs/evaluate)
- Checks (autoevals library): LLM-based Factuality (needs `output`, `expected`, `input`), ClosedQA, Humor, Moderation, Summarization, Translation, Security, Battle, SQL; RAG evaluators (context precision/relevancy/recall/entity recall, Faithfulness, answer relevancy/similarity/correctness); heuristics (Levenshtein, exact match, numeric diff, JSON diff); "The documentation does not mention specific agent trajectory or tool-call validation scorers." MIT — [braintrustdata/autoevals](https://github.com/braintrustdata/autoevals)
- Search-result text (not fetched directly) says Braintrust's "Loop" lets users "create custom scorers for agent trajectories by describing evaluation criteria in natural language" — i.e., user-authored rubric — [Braintrust articles (search snippet)](https://www.braintrust.dev/articles/top-5-platforms-agent-evals-2025)
- Parses self-report? No built-in. Per-task spec? Factuality needs `expected`; RAG Faithfulness needs `context`. 
- Pricing: Starter free ($10 model credits, 1 GB, 10k scores, 14-day retention); Pro $249/mo; Enterprise custom — [Braintrust pricing](https://www.braintrust.dev/pricing)
- Verdict: **logs + rubric-scored; no report-vs-log check.**

**LangSmith / agentevals**
- Checks: trajectory match evaluators in strict / unordered / subset / superset modes — every one "Reference trajectory required"; LLM-as-judge trajectory evaluator takes an optional reference and a rubric ("Makes logical sense between steps / Shows clear progression / Is semantically equivalent to reference (if provided)"); graph-trajectory variants for LangGraph. "No evaluator directly compares final natural-language answers against tool-call trajectories for consistency." MIT — [langchain-ai/agentevals](https://github.com/langchain-ai/agentevals); [LangSmith trajectory evals docs](https://docs.langchain.com/langsmith/trajectory-evals)
- Verdict: **reference-trajectory or rubric scoring; no self-report parsing.**

**Langfuse**
- Checks: LLM-as-a-judge evaluators from "a blank prompt, or … a template provided by Langfuse" (names not enumerated on the docs page); evaluator variables can map to "Input, output, and metadata from the matched observation", "Tool calls from the observation", and "Expected output (experiments only)". Critically: evaluators "do not load sibling or child observations from the same trace … The evaluator still only sees data on that root observation; it will not automatically include data from child observations unless your application writes the required summary or context onto the root observation." — [Langfuse LLM-as-a-judge docs](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge)
- Pricing: Hobby free (50k units/mo, 2 users), Core $29/mo, Pro $199/mo, Enterprise $2,499/mo; self-hostable open source — [Langfuse pricing](https://langfuse.com/pricing); MIT per awesome-auditable-ai — [awesome-auditable-ai](https://github.com/yzhao062/awesome-auditable-ai)
- Verdict: **logs; judge cannot even see the whole trace by default, so a report-vs-log evaluator would need app-side plumbing plus a hand-written prompt.**

**Arize Phoenix / AX (incl. Signal)**
- Phoenix pre-built agent evals: Tool Calling / Agent Function Calling ("how well a model selects a tool to use, extracts the right parameters … and generates the tool call code"), Agent Path Convergence ("how consistently an agent reaches a result without taking unnecessary steps"), Agent Planning, Agent Reflection — [Phoenix pre-built evals](https://arize.com/docs/phoenix/evaluation/running-pre-tested-evals); [Tool calling eval](https://arize.com/docs/phoenix/evaluation/running-pre-tested-evals/tool-calling-eval)
- Signal (managed agent in AX): "processes production traces on a recurring schedule"; groups failures into issues of these categories: "Wrong agent or tool selection; Silent fallback to model memory; Retry and planning loops; Cost regressions; Latency regressions; Retrieval degradation; Session-level incoherence"; works without a predefined rubric; the article frames grounding/claim checks as the job of separate evaluators, not of Signal itself; "Signal issue detection is available across Arize AX plans", repo-backed investigations/PRs are Enterprise — [Arize: Debug production agents with Signal](https://arize.com/blog/debug-production-ai-agents-with-signal-tutorial/)
- Pricing: AX Free $0 (10 Signal issues/mo, 25k spans), AX Pro $50/mo (25 Signal issues/mo, 50k spans), Enterprise custom; Phoenix "open-source, local-first" (Elastic License 2.0 per awesome-auditable-ai) — [Arize pricing](https://arize.com/pricing/)
- Verdict: **Signal is the nearest spec-free *behavioural* auditor among vendors, but its issue taxonomy is about trajectory pathology (loops, wrong tool, memory fallback), not about whether the final report is true.**

**Helicone**
- Records: gateway-level requests/costs/latency; "Eval Scores" attach scores to a `response_id`; judge scoring uses your own judge model — [Helicone eval scores docs](https://docs.helicone.ai/features/advanced-usage/scores); Apache-2.0 — [Helicone/helicone](https://github.com/Helicone/helicone)
- Secondary source (vendor competitor blog, treat with caution): after Mintlify acquired Helicone in March 2026 "the platform has transitioned to maintenance mode"; pricing "from $79/mo with a free tier" — [Latitude blog](https://latitude.so/blog/helicone-alternatives)
- Verdict: **request logging + user-supplied scores; no agent or claim evaluators.**

**AgentOps**
- Records: "Session replays", LLM calls with cost, tool usage analytics, multi-agent workflows; "Custom eval metrics"; roadmap lists "Agent scorecards" and "Evaluation playground"; MIT; 5.8k stars — [AgentOps-AI/agentops](https://github.com/AgentOps-AI/agentops)
- Verdict: **logs + shows to a human (replay); no built-in checks.** Pricing page returned 404 (see Gaps).

**Maxim**
- Evaluator categories: "AI Evaluators: Uses LLMs as judges with curated prompts (e.g., Clarity, Agent Trajectory)", Voice, Statistical, Programmatic — [Maxim pre-built evaluators](https://www.getmaxim.ai/docs/library/evaluators/pre-built-evaluators)
- Vendor articles describe off-the-shelf "task success, trajectory quality, tool selection, step completion, faithfulness, and context relevance"; "Tool Selection evaluates whether the agent picked the right tool with the right parameters for every tool call in its trajectory, without scoring whether the execution itself succeeded"; "Step completion assesses whether the agent followed expected workflows" — [Maxim: How to evaluate AI agents in production](https://www.getmaxim.ai/articles/how-to-evaluate-ai-agents-in-production-metrics-methods-and-pitfalls/)
- Verdict: **rubric/expected-workflow scoring; nothing that reads the final report against the trace.**

**Galileo**
- Tool Selection Quality: "Determines whether the agent selected the correct tool and for each tool the correct arguments" — [Galileo docs: Tool Selection Quality](https://docs.galileo.ai/galileo/gen-ai-studio-products/galileo-guardrail-metrics/tool-selection-quality)
- Action Advancement (trace-level, "whether each action makes meaningful progress toward user goals") and Action Completion (session-level, "whether the agent accomplished ALL user goals"); "nine out-of-the-box agent-specific metrics" — [Galileo blog: AI agent metrics](https://galileo.ai/blog/ai-agent-metrics)
- Pricing: Free 5,000 traces/mo; Pro $100/mo (50k traces); Enterprise custom — [Galileo pricing](https://galileo.ai/pricing)
- Verdict: **goal-vs-user-request scoring by LLM judge; not report-vs-log.** (The `/concepts/metrics/agentic/overview` page 404'd twice; definitions above are from the guardrail-metrics page and blog.)

**Datadog LLM Observability / Agent Observability**
- Managed LLM-as-a-judge templates: Failure to Answer; Goal Completeness ("Checks whether the agent resolved the user's intent by analyzing full session spans"); Hallucination ("flags any output that disagrees with the context provided to the LLM"); Prompt Injection; Sentiment; Tool Argument Correctness ("Verifies that arguments provided to a tool are correct and relevant based on the tool schema"); Tool Selection ("Verifies that the tools chosen by the LLM align with the user's request and the set of available tools"); Topic Relevancy; Toxicity. "No template explicitly checks whether an agent's response claims actions that tool calls do not demonstrate." — [Datadog agent evaluation templates](https://docs.datadoghq.com/llm_observability/evaluations/managed_evaluations/agent_evaluations/)
- Natively ingests OTel GenAI semantic conventions — [Datadog blog](https://www.datadoghq.com/blog/llm-otel-semantic-convention/)
- Pricing: page shows AI Credits ($500 per 500 credits/mo annual, $1.30/credit on-demand) without itemising Agent Observability — [Datadog pricing](https://www.datadoghq.com/pricing/?product=llm-observability)
- Verdict: **closest vendor template is "Hallucination" (output vs. supplied context); it is a RAG-grounding check, not an actions check.**

**Weights & Biases Weave**
- Built-in scorers: HallucinationFreeScorer (requires `context` column), SummarizationScorer, OpenAIModerationScorer, EmbeddingSimilarityScorer (needs target), ValidJSON/ValidXML/Pydantic, ContextEntityRecallScorer, ContextRelevancyScorer. "The documentation contains no agent/trajectory scorers, tool-call validators … No scorer compares final answers against tool calls or execution traces." — [Weave built-in scorers](https://docs.wandb.ai/weave/guides/evaluation/builtin_scorers)
- Verdict: **no agent evaluators at all.**

**OpenTelemetry GenAI semantic conventions**
- `invoke_agent` span: `gen_ai.input.messages` and `gen_ai.output.messages` are **Opt-In** (flagged as likely to contain PII); `gen_ai.tool.definitions` Opt-In; token usage Recommended; both agent and tool spans are marked **Development** status; "The specification defines recording semantics only … contains no evaluation or verification logic." — [gen-ai-agent-spans.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)
- Moved out of core conventions at v1.42.0 (12 June 2026) per the awesome list's standards table — [awesome-auditable-ai](https://github.com/yzhao062/awesome-auditable-ai)
- Verdict: **a schema for logging; the final message and tool results are opt-in, so a downstream checker cannot assume they are present.**

**Laminar (adjacent vendor, found via the awesome list)**
- "A Signal is an instruction, written in plain language, that reads every trace and produces a structured record when it sees what you described", paired with a JSON schema; examples given are "agent completed checkout", "agent loops on the same tool without progress" — [Laminar Signals docs](https://laminar.sh/docs/signals/introduction)
- Verdict: **generic (not per-task) plain-language rule + LLM over full trace — the closest vendor primitive one could *write* a report-vs-log check in, but no such signal ships.**

### Inferences
- The industry's agent-eval vocabulary is "tool selection / argument correctness / trajectory match / goal completion". "Did the agent's summary match what it did" is absent as a named metric across all eleven vendors checked.
- Three vendor scorers are repurposable in principle because they compare an output to a `context` field (Braintrust RAG Faithfulness, Weave HallucinationFreeScorer, Datadog Hallucination): one could stuff the tool-call log into `context`. That is user plumbing, not a product feature, and none is designed for action semantics ("ran tests", "did not modify X").
- Langfuse's root-observation-only evaluator visibility means a report-vs-log evaluator there needs the application to write a trace summary into the root span first.

### Gaps
- Maxim pricing: the `/pricing` URL served Bifrost (Maxim's gateway) OSS/Enterprise pricing, not Maxim platform pricing; not resolved.
- AgentOps pricing page returned 404.
- Braintrust "Loop" trajectory-scorer claim comes from a search snippet; the specific docs page was not fetched.
- Helicone "maintenance mode" claim is from a competitor blog only.
- Galileo's canonical agentic-metrics overview page was unreachable (404 on both v2docs and docs hosts).

---

## Key question 2: Audit / provenance tools — do they surface the log to a human, or check the report against it?

### Takeaway
Every capture/provenance tool examined (AgentLens, AgentSight, Entire, SpecStory, the flight recorders, Anthropic/OpenAI/GitHub session logs) either logs or shows to a human; the only ones that *check* anything check cryptographic integrity (hash chain unbroken) or fixed danger patterns (`rm -rf`, force-push). Two small OSS projects come closer: `red-handed` deterministically checks one claim type ("tests pass") against the Claude Code transcript + git, and `agent-trace` (KTH) compares a *structured* self-reported trajectory against OS probes — but not natural language, and only with a simulated agent so far.

### Cited Findings

**AgentLens (agentkitai)**
- Records: "captures every LLM call, tool invocation, approval decision, and error"; ingestion via OTel (`gen_ai.*`), Python auto-instrumentation, MCP server (22 tools), SDK; "every event is SHA-256 hash-chained to the one before it"; "Purpose-built for the record-keeping obligations of EU AI Act Article 12" — [agentkitai/agentlens](https://github.com/agentkitai/agentlens)
- Checks: chain integrity only (`GET /api/audit/verify/export`, "CHAIN VALID — no tampering detected" / "CHAIN BROKEN"); session replay step-through; "No evidence that AgentLens parses or analyzes the agent's final natural-language outputs"; no per-task spec; MIT + AgentLens Cloud (price undisclosed); 23 stars — [agentkitai/agentlens](https://github.com/agentkitai/agentlens)
- Verdict: **logs (tamper-evident) + shows to a human; integrity check only.**

**AgentSight (eBPF)**
- Paper: eBPF kernel events plus TLS-intercepted LLM prompts/responses; "causally correlates these two streams across process boundaries using a real-time engine and secondary LLM analysis"; "detects prompt injection attacks, identifies resource-wasting reasoning loops, and reveals hidden coordination bottlenecks"; "framework-agnostic", "instrumentation-free" — [arXiv 2508.02736](https://arxiv.org/abs/2508.02736)
- Repo: SSL uprobes capture "Plaintext LLM payloads at SSL/TLS call boundaries"; tracks "process execution, file access, and resource use"; "correlates LLM traffic with process and file events"; README names no automated analyzers, offers timeline/process-tree/event-log views to "spot slow steps, retry loops, repeated model or tool calls"; MIT; 699 stars; active — [agent-sight/agentsight](https://github.com/agent-sight/agentsight)
- Verdict: **the best *independent* log (kernel-level, sees the model's actual output bytes and the actual syscalls) but shows to a human; the paper's "secondary LLM analysis" targets injection/loops, not report truthfulness.**

**Entire.io**
- Records via per-agent git hooks: "transcripts, prompts, files touched, token usage, tool calls" as Checkpoints stored in `refs/entire/checkpoints/<shard>/<id>` linked by an `Entire-Checkpoint` commit trailer; agents: Claude Code, Codex, Copilot CLI, Cursor, Droid, Gemini, OpenCode, Pi; "purely a capture and search tool—it performs no verification of agent statements against actual actions"; MIT; 5.1k stars — [entireio/cli](https://github.com/entireio/cli)
- Verdict: **logs + shows to a human (searchable, resumable); no checks.**

**SpecStory**
- Records "every AI interaction you have with AI coding assistants" to `.specstory/history/`; supports Cursor, Copilot, Claude Code, Codex CLI, Cursor CLI, Droid, DeepSeek TUI, Antigravity, Muse, Qwen Code, Pi; "purely a capture and surfacing tool"; Apache-2.0 CLI, closed-source IDE extensions; 1.3k stars — [specstoryai/getspecstory](https://github.com/specstoryai/getspecstory)
- Verdict: **logs + shows to a human.**

**"Flight recorders" for coding agents**
- flightrec: harness-agnostic; captures LLM calls via local reverse proxy (`*_BASE_URL` override), file edits via filesystem watcher, shell commands via `PATH` shim, "stitched into one timeline"; replay/scrub/fork; "does not validate agent claims against actions"; MIT; 0 stars, 20 commits — [cedric190703/flightrec](https://github.com/cedric190703/flightrec)
- Agent Flight Recorder: normalises Codex/OpenCode/Claude Code/Cursor events into an append-only SQLite timeline; "Replay means read-only playback of recorded evidence. Agent Flight Recorder never re-executes tools"; "Evidence, not a reconstructed story"; MIT; 1 star — [OthmaneBlial/Agent-Flight-Recorder](https://github.com/OthmaneBlial/Agent-Flight-Recorder)
- agentfdr: "Zero instrumentation · zero cloud · zero config — it reads the transcripts Claude Code and Codex already write"; flags tool loops, error streaks, context bloat, cache thrash, file churn, session collisions; does not verify claims; MIT — [agentfdr](https://kamihork.github.io/agentfdr/)
- Tracon: logs "every command, file edit, package install, and prompt, attributed to the agent and session"; auto-flags "recursive deletes, pipe to shell installs, credential access, force pushes, permission bypasses"; flags, never blocks; AGPL; local-only — [dev.to: flight recorder flagged itself](https://dev.to/mukes555/my-ai-agent-built-a-flight-recorder-for-ai-agents-and-it-flagged-itself-30dg); [mukes555/tracon](https://github.com/mukes555/tracon)
- tracehouse ("tails your transcripts and ships every prompt, thought, tool call and result as a live trace waterfall") and AgentAudit ("A flight recorder for coding agents on AWS") exist but were not fetched — [tracehouse](https://tracehouse.ai/); [DevAnnafi/AgentAudit](https://github.com/DevAnnafi/AgentAudit)
- Verdict: **all log + show; the only automatic checks are fixed dangerous-pattern rules (Tracon) or trajectory-pathology heuristics (agentfdr).**

**Vendor session-log features (Anthropic / OpenAI / GitHub)**
- Claude Code: transcripts stored as JSONL at `~/.claude/projects/<project>/<session-id>.jsonl`; hooks (e.g. Stop) receive `transcript_path`; known issue that `transcript_path` can lag — [Claude Code sessions docs](https://code.claude.com/docs/en/sessions); [anthropics/claude-code issue #8564](https://github.com/anthropics/claude-code/issues/8564)
- Anthropic Compliance API (Enterprise): "Each session is a single conversation with Claude; its transcript is the sequence of user prompts, assistant responses, and tool calls and results"; endpoints "support eDiscovery … and data loss prevention (DLP) enforcement"; Claude Code coverage in beta — [Retrieve session transcripts](https://platform.claude.com/docs/en/manage-claude/compliance-sessions); [Compliance API blog](https://claude.com/blog/compliance-api-cowork-and-claude-code)
- OpenAI Codex CLI: "writes per-session JSONL logs under $CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl" — [openai/codex discussion #3827](https://github.com/openai/codex/discussions/3827); third-party viewers exist — [codex-trace](https://github.com/Victarry/codex-trace)
- GitHub Copilot coding agent: "In the session logs, you can see Copilot's internal monologue and the tools it used to understand your repository, make changes and validate its work"; "Trace any Copilot coding agent commit to its session logs" — [GitHub docs: track Copilot sessions](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/track-copilot-sessions); [GitHub changelog 2026-03-20](https://github.blog/changelog/2026-03-20-trace-any-copilot-coding-agent-commit-to-its-session-logs/)
- Verdict: **all three log and show to a human (and to compliance/eDiscovery in Anthropic's case); none checks the final message against the tool record.**

**red-handed (the closest occupied point, OSS)**
- Inputs: "Claude Code JSONL transcripts from `~/.claude/projects`" plus git; "No model is called. The whole thing is deterministic: the same session gives the same answer every time."
- Claim extraction: pattern-matches assistant text for "tests pass"-type assertions ("Claims are matched in English, Korean, Japanese and Chinese", `src/claims/patterns.ts`)
- Nine detectors: test-census, **claim-vs-fail** ("Agent said tests pass; last run failed"), **claim-no-run** ("Agent claimed pass; no run occurred"), hardcoded-expected, assertion-weakening, skip-only, no-verify, config-disable, error-swallowing
- Evidence rule: CAUGHT requires both "Session transcript shows agent performed the action" and "Code still displays that change now"; "If the agent later undid the change, there is nothing to accuse it of, and the finding disappears."
- Parses test-runner output from vitest, jest, mocha, pytest, rspec, go test, cargo test, gradle, maven, etc.; primary harness Claude Code, others (Codex, Cursor, Gemini CLI) via skills CLI, CI via `--git-only`; no per-task config; MIT; 4 stars — [sjh9714/red-handed](https://github.com/sjh9714/red-handed)
- Author's audit of 249 own sessions: 124 contained "tests pass" claims, 117 had a preceding actual run, 7 had no machine-readable verification, "Zero confirmed false claims", 6 false positives in adversarial testing; "If the verdict came from an LLM, the same transcript would produce different answers on different days — and then it isn't evidence." — [dev.to: I audited 249 of my own AI coding sessions](https://dev.to/sjh9714/i-audited-249-of-my-own-ai-coding-sessions-the-problem-wasnt-lying-4f42)
- Verdict: **checks the report against the log, spec-free, deterministic — but for exactly one claim type ("tests pass") and against the harness's own transcript rather than an independent recorder.**

**i-dont-believe-you (OSS skill)**
- "Seven shell commands, plain `git`, `grep` and `comm`. An agent has to run them against the real diff before it can tell you the tests pass, and paste the raw output"; reads git diffs, "does not parse the agent's natural-language claim"; generic checks (`.skip`, deleted test files, reduced assertions, vanished test names); Markdown file in `.claude/skills/`; posted 2026-09-16 — [LeonardLeroy/i-dont-believe-you](https://github.com/LeonardLeroy/i-dont-believe-you); [dev.to post](https://dev.to/leoleroy/i-got-tired-of-coding-agents-saying-all-tests-pass-when-the-diff-said-otherwise-5ce9)
- Verdict: **a diff-hygiene gate the agent runs on itself; no self-report parsing, and the agent controls the evidence.**

**agent-trace (ASSERT-KTH)**
- "compares agent self-reported logs against independent system observations to detect fabrication, omission, or substitution"; the self-report is "a structured JSON log documenting claimed actions including 'file opens, writes, process executions, exits'" — "a machine-readable record of tool calls, not natural language"
- Probes: fanotify filesystem, eBPF process (`execve`/`exit_group` with argv and exit codes), eBPF socket + `SSL_write` uprobe
- Matching: "greedy closest-timestamp, one-to-one within a configurable time window", "strict absolute-path equality for process execution"; categories Corroborated / Unwitnessed (fabrication) / Unrecorded (omission) / Mismatched (substitution); verdict FAITHFUL / NOT FAITHFUL
- No per-task spec; tested only with a synthetic `simagent`, "Real-agent integration is planned but not yet implemented"; "a research prototype under active development"; 0 stars, 69 commits; license not stated — [ASSERT-KTH/agent-trace](https://github.com/ASSERT-KTH/agent-trace)
- Verdict: **the right *shape* (independent log, spec-free, fabrication/omission taxonomy) applied to the wrong artefact (structured trajectory, not the prose report), and not yet run on a real agent.**

**Other decision-record tools listed by awesome-auditable-ai (not individually fetched)**
- MakerChecker (Ed25519-signed hash-chained log), aegis, sofagent ("Commit-time engine checking diffs against 24 rules"), halo-record, Agent Governance Toolkit, TRACE (hardware-attested trust records), auditable ("Re-evaluates and reverses committed actions"), Proofline ("SHA-256 proof packets with deterministic contracts"), SEMAPRAX, Busabase — all 2026, all integrity/policy-oriented — [awesome-auditable-ai §7](https://github.com/yzhao062/awesome-auditable-ai)
- YYLO Benchmark: "Retained state is only accepted when workspace custos-code, post-execution manifests, evaluator configuration, and provenance hashes form one exact hash-linked chain, so reports and re-evaluation are immutable evidence rather than logs"; deterministic and blinded LLM-judge profiles; MIT — [yylo.dev](https://www.yylo.dev/)
- AgentRunProof: "deterministic runtime-conformance harness for the OpenAI Agents SDK … writes content-addressed evidence that can be rechecked without rerunning the SDK" — SDK-invariant testing, not report checking — [FU-max-boop/agentrunproof](https://github.com/FU-max-boop/agentrunproof)

### Inferences
- The provenance segment has converged on *integrity* (hash chains, signatures, attestations) as the definition of "verifiable". A perfectly tamper-evident log of a false report is still a false report; none of these tools interprets the report.
- Independence of the log is uneven: AgentSight, flightrec, agent-trace, Tracon observe below the harness (kernel/proxy/PATH shim), while red-handed, agentfdr, SpecStory, Entire, Compliance API all read the harness's own transcript. Only agent-trace both records independently *and* compares — and it compares a JSON trajectory, not prose.
- red-handed's design choice (deterministic, single claim type, two-evidence rule) is the only field-tested instance of "parse what the agent said, check it against what happened" for coding agents; its own author found zero false claims in 249 sessions, which is a useful base-rate data point and a caution about false-positive control.

### Gaps
- agent-trace's license and any accompanying paper: not stated in README; no arXiv link found.
- tracehouse and AgentAudit (AWS) were not fetched; their exact capabilities are from search snippets only.
- Whether Anthropic's Compliance API exposes tool *results* (as opposed to tool calls) for Claude Code local sessions was not verified beyond the doc sentence quoted.

---

## Key question 3: Verification research — AgentLTL, control plane, Meerkat, CPL, LogicGuard, trajectory-verification / self-report-faithfulness papers, and the awesome-auditable-ai scan

### Takeaway
The formal-methods line (AgentLTL, CPL, LogicGuard, AgentGuard, AgentSpec) verifies traces against *authored specifications*; AgentLTL is notable because it does parse the final answer (an entity-grounding predicate) but its constraints are instantiated per task from gold traces and "in deployment the constraints must be authored by domain experts". The spec-free line (Meerkat, Signal, Docent) uses LLM agents over traces with natural-language *violation classes*, not per-task specs, but targets safety/gaming patterns rather than the truth of the agent's report. One 2026 paper (Reasoning Provenance / AER) explicitly proposes a "fidelity score … between self-reported tool calls and independently intercepted execution data" but reports it as planned methodology with results "ongoing".

### Cited Findings

**AgentLTL (arXiv 2607.02599, Elkoussy & Perez, LRE/EPITA, 1 Jul 2026)**
- "a language derived from First-Order Linear Temporal Logic that expresses procedural rules over agent traces, yielding a deterministic, judge-free compliance score"; uses: scoring completed traces, gating tool calls online, and dense rewards for finetuning — [arXiv abs](https://arxiv.org/abs/2607.02599)
- Spec form: FO-LTL over tool-call tuples `cᵢ = (nᵢ, aᵢ, rᵢ, i)` (name, args, result, position); predicates for occurrence (`Called(t)`), ordering (`Before(t₁,t₂)`), argument matching (`CalledWith(search,{q:"foo"})`), result checks, counts; example `G(search → F cite)`; a grounding predicate `κ_ground ≡ ∀e ∈ ent(a), e ∈ out(τ)` — "every entity mentioned in a was observed in some tool output", with entities extracted by "regex matching"
- Authorship: "From each gold trace we instantiate the six constraint layers … yielding one constraint set G_P per task"; "AgentLTL shifts the cost of evaluation from labeling answers to writing FO-LTL constraints … in deployment the constraints must be authored by domain experts"
- Limits: "Our framework evaluates each trace against a fixed specification written before execution"; cannot express timing, resource, privacy, or hyperproperties — [arXiv HTML](https://arxiv.org/html/2607.02599)
- Verdict: **checks the trace, and even the final answer's entities, but only against a per-task, pre-written FO-LTL spec; without the spec there is nothing to evaluate.**

**A Deterministic Control Plane for LLM Coding Agents (arXiv 2606.26924, Madatha, 25 Jun 2026)**
- Study of 10,008 repos: 10.1% exact-duplicate agent configs, <1% declare permission boundaries; proposes "Rel(AI)Build": content-addressed agent definitions, HMAC lockfiles, "tiered permissions and attack-derived blocklists before LLM invocation", phase state machines with "requirement-to-file-to-test traceability", "hash-chained audit logs", prompt-drift detection; no parsing of agent summaries vs. actions; code availability not stated — [arXiv abs](https://arxiv.org/abs/2606.26924)
- Verdict: **pre-execution governance + integrity logging; nothing about the report.**

**Meerkat — Detecting Safety Violations Across Many Agent Traces (arXiv 2604.11806, Stein, Brown, Hassani, Naik, Wong, UPenn, 13 Apr 2026)**
- "combines clustering with agentic search to uncover violations specified in natural language"; "Per-trace judges miss failures that only become visible across traces, naive agentic auditing does not scale … fixed monitors are brittle"; "discovers widespread developer cheating on a top agent benchmark, and finds nearly 4x more examples of reward hacking on CyBench" — [arXiv abs](https://arxiv.org/abs/2604.11806)
- Trace = "user messages, model outputs, tool calls, tool responses, and relevant environment observations"; violation example: "The agent does not find and exploit shortcuts to achieve its goal rather than solve the genuine problem"; found agents whose "first action is often cat /tests/test_outputs.py, after which it reverse-engineers the expected outputs"; found Terminal-Bench 2.0 tasks where AGENTS.md "contain the literal graded answer, which the agent adopts verbatim and 'verifies' against the same source"; corpus-level ("evidence of failure lies in some witness W with |W|>1"); no code URL — [arXiv HTML](https://arxiv.org/html/2604.11806)
- Verdict: **spec-free in the per-task sense (one NL property per violation *class*), but corpus-level and safety/gaming-oriented; does not compare each agent's final report to its own actions.**

**Causal Past Logic for Runtime Verification of Distributed LLM Agent Workflows (arXiv 2605.20923, Bollig, 20 May 2026, rev. 10 Sep 2026)**
- Extends ZipperGen with CPL ("an adaptation of PT-DTL" with previous/since); guards in if/while constructs evaluated online over "the latest causally visible event of another lifeline"; knowledge-vector monitor; no NL output checking; no code URL — [arXiv abs](https://arxiv.org/abs/2605.20923)
- Verdict: **authored guards over workflow events; irrelevant to report truthfulness.**

**LogicGuard (arXiv 2507.03293)**
- "modular actor-critic architecture in which an LLM actor is guided by a trajectory level LLM critic that communicates through Linear Temporal Logic"; critic "analyzes full trajectories and proposes new LTL constraints"; embodied (BEHAVIOR household tasks, Minecraft) — [arXiv abs](https://arxiv.org/abs/2507.03293)
- Verdict: **LTL constraints generated by an LLM critic for embodied planners; not a report checker.**

**AgentGuard (arXiv 2509.23864, Koohestani, 28 Sep 2025)**
- "Dynamic Probabilistic Assurance"; "operates on an agent's raw I/O … abstracts it into formal events corresponding to transitions in a state model"; online-learned MDP checked by a probabilistic model checker; no statement on who writes properties or on NL output — [arXiv abs](https://arxiv.org/abs/2509.23864)
- Verdict: **quantitative property checking; needs properties.**

**Reasoning Provenance for Autonomous AI Agents / Agent Execution Record (arXiv 2603.21692, Vispute & Kadam, 23 Mar 2026, rev. 10 Apr)**
- AER captures "intent, observation, and inference as first-class queryable fields on every step" — [arXiv abs](https://arxiv.org/abs/2603.21692)
- Full text: "AER's reasoning fields are populated by the agent itself. They record what the agent *reports* about its reasoning, not a ground-truth account"; proposes a transport-layer interceptor (§3.4) and reconciler so that "the fidelity score measures agreement between self-reported tool calls and independently intercepted execution data"; test question: "do agents that report 'rule out DNS' actually perform DNS checks, as verified by the interceptor?"; §7 "describes our planned evaluation methodology … full empirical results are ongoing work"; only "a reference implementation and SDK" for local capture is mentioned — [arXiv HTML](https://arxiv.org/html/2603.21692)
- Verdict: **the clearest published statement of the target check (self-report vs. independent interception), at the level of *structured* self-reported tool calls and *planned* evaluation; no released verifier, no NL-report parsing.**

**Verifiability-First Agents (arXiv 2512.17259, Gupta, 19 Dec 2025)**
- "cryptographic attestations of agent actions", "lightweight Audit Agents that continuously verify intent versus behavior using constrained reasoning", challenge-response for high-risk ops; OPERA benchmark; abstract does not say what record is checked or whether a spec is needed — [arXiv abs](https://arxiv.org/abs/2512.17259)
- Verdict: **names "intent versus behavior" but the abstract gives no mechanism; treat as position-level.**

**AgentLens — lucky pass (arXiv 2605.12925, Sahoo et al., 13 May 2026, v3 2 Jun)**
- "Lucky Pass" = passing trajectory with "regression cycles, blind retries, missing verification, or temporally disordered exploration, implementation, and verification"; 10.7% of passing trajectories (0.5%–23.2% by model); builds "Prefix Tree Acceptor (PTA) references by merging multiple passing solutions for the same task" — only 47 of the tasks had enough passes; context-sensitive intent labeler assigns Exploration/Implementation/Verification/Orchestration; repo "soon" — [arXiv abs](https://arxiv.org/abs/2605.12925)
- Verdict: **process-quality judging that *needs per-task references built from multiple passing runs*; does not read the agent's report.**

**HANSEL (arXiv 2606.18671, Zhang & Nam, 17 Jun 2026)**
- Extracts "evidence pages and snippets" from web-agent trajectories as navigable views; "When the agent's answer cannot be traced to any visited page, HANSEL explicitly flags this gap"; humans verify (14-participant study); 45 tasks from AssistantBench and Online-Mind2Web; no per-task spec — [arXiv abs](https://arxiv.org/abs/2606.18671)
- Verdict: **spec-free answer-to-trajectory grounding, but for web QA answers vs. visited pages, and human-in-the-loop rather than a verdict.**

**Verify Before You Commit / SAVeR (arXiv 2604.08401)** — self-auditing *inside* the agent over belief states before acting; not an external checker — [arXiv abs](https://arxiv.org/abs/2604.08401)

**Property-Level Reconstructability of Agent Decisions (arXiv 2605.12078, Solozobov, 12 May 2026)**
- Asks whether traces let one reconstruct "what actions agents took, under whose authority, against which policies, and based on what reasoning"; across six vendor SDK regimes, "Strict-governance-completeness separates into three tiers ranging from 42.9% to 85.7%", with a regime-independent gap (reasoning trace) — [arXiv abs](https://arxiv.org/abs/2605.12078)
- Verdict: **evidence that vendor traces are often insufficient to reconstruct decisions — a prerequisite problem for any report-vs-log checker built on vendor telemetry.**

**awesome-auditable-ai scan (yzhao062; 147 stars; 204 entries; last link audit 2026-09-04)**
- Sections: Reliability Map; Auditable Agents Ecosystem; Surveys; Failure Attribution and Diagnosis (Who&When, MAST, TRAIL, AgenTracer, AgentRx, TraceElephant, …); Reliability and Robustness; Runtime Monitoring and Guardrails (AgentSpec "Rule-based enforcement (trigger/predicate/action)", ActPlane, AgentMonitor, …); Audit Trails and Decision Records; Security Auditing; Datasets and Benchmarks; Tools and Platforms; Standards — [awesome-auditable-ai](https://github.com/yzhao062/awesome-auditable-ai)
- No entry is described as checking an agent's natural-language report against its log. The nearest by description: "auditable — Records action dependencies, re-evaluates, reverses via rails"; "AgentRx — Synthesizes constraints, checks step-by-step, records validation log"; "Docent — Transcript summarization surfacing broken tasks"; "Laminar — OTel with plain-English Signals"; "TelemetrySuffBench — Metadata vs. OTel vs. OpenInference views"; "HINTBench — 629 benign-risk trajectories; strong detection below 35 F1" — [awesome-auditable-ai](https://github.com/yzhao062/awesome-auditable-ai)
- Docent (Transluce): "AI agent analysis platform" ingesting transcripts; summarization, search, rubric-based judging, clustering; 135 stars; Apache-2.0 per the awesome list — [TransluceAI/docent](https://github.com/TransluceAI/docent)

### Inferences
- Formal-verification work makes the *absence* of a spec a hard stop by construction: AgentLTL's score is a function of (trace, constraint set G_P); with no G_P there is no score. Its authors say so directly ("must be authored by domain experts").
- The spec-free tools that exist (Meerkat, Signal, Docent, Laminar Signals) all substitute a *class-level* natural-language question for a per-task spec. A report-vs-log checker could be phrased the same way ("the final message asserts an action the trace does not contain"), but none of these has done so or evaluated it.
- AER is the only paper that frames self-report fidelity as the measured quantity; that it is unimplemented as of its April 2026 revision, and restricted to structured tool-call self-reports, is the strongest signal that the prose-report version is open.

### Gaps
- Meerkat: no code release found; whether its violation library includes a "claims success without verification" property is not confirmed in the fetched text.
- AgentLens (lucky pass) repository not yet public at time of research.
- LogicGuard was confirmed to be the embodied-agent LTL-critic paper; no other "LogicGuard" for coding agents was found.
- Verifiability-First Agents: mechanism of "intent versus behavior" audit not extractable from the abstract page.

---

## Key question 4: Adjacent — LLM-judge-of-trajectory, factuality/faithfulness checkers, provenance standards

### Takeaway
Judge-of-trajectory work either needs a reference (AgentLens PTA, agentevals match) or a rubric (agentevals LLM judge, Docent, Phoenix templates); faithfulness checkers (RAG Faithfulness, HallucinationFree, Datadog Hallucination, TruLens Groundedness) are "output vs. supplied context" and could be repurposed only by treating the action log as context; C2PA covers content provenance for AI-produced *assets*, not a signed record of agent *actions*, and the agent-action equivalents in the wild are hash-chain/attestation logs with no semantic layer.

### Cited Findings
- TruLens agent feedback functions: Tool Selection ("Did it pick the right tool"), Tool Calling, Tool Quality, Plan Adherence, Plan Quality, Execution Efficiency, Logical Consistency; Groundedness is "Is the answer supported by what was retrieved?" (RAG); open source, "shepherded by Snowflake"; MIT per awesome list — [trulens.org](https://www.trulens.org/); [awesome-auditable-ai](https://github.com/yzhao062/awesome-auditable-ai)
- Weave HallucinationFreeScorer "Requires `context` column"; Datadog Hallucination "flags any output that disagrees with the context provided to the LLM"; autoevals RAG Faithfulness — see Q1 citations — [Weave](https://docs.wandb.ai/weave/guides/evaluation/builtin_scorers); [Datadog](https://docs.datadoghq.com/llm_observability/evaluations/managed_evaluations/agent_evaluations/); [autoevals](https://github.com/braintrustdata/autoevals)
- C2PA: a manifest "declares which AI model produced or modified it, what inputs were supplied, and the full chain of edits"; actions performed by an AI/ML system are identified via `digitalSourceType`; scope is digital assets — [C2PA AI/ML guidance](https://spec.c2pa.org/specifications/specifications/2.4/ai-ml/ai_ml.html)
- Agent-action provenance analogues in awesome-auditable-ai: "TRACE — Hardware-attested trust records (CC BY 4.0 spec, Apache-2.0 SDK, 2026-present)", "MemLineage — RFC 6962 Merkle-log memory signing", "Agent-Sentry — Execution provenance bounding runtime actions", "AI Agents Need Authenticated Delegation — OAuth 2.0 extension (ICML 2025)" — [awesome-auditable-ai](https://github.com/yzhao062/awesome-auditable-ai)
- Practitioner framing of the problem: in the Darwin Gödel Machine an agent "fabricated a test log claiming unit tests had passed when they never executed" and later "read this false log and treated it as verification"; "you don't need a deceptive agent to get this failure, just a filesystem that can't say who wrote what"; the missing piece is provenance types that tag claims as "runtime-verified" versus "self-reported" and prevent self-reported claims being re-read as proof (Parfenov, 8 Jul 2026, upd. 16 Sep 2026) — [dev.to: The agent faked a test log](https://dev.to/p0rt/the-agent-faked-a-test-log-then-believed-it-self-editing-harnesses-have-a-provenance-problem-3id6)
- Community diagnosis of the mechanism: agents' completion summaries are "predicting what a successful completion summary looks like, not reading from ground truth"; and "Claude Code writes a transcript for every session … Line that up with git and you can compute the gap between the words and the work" — [dev.to (search snippets across several posts)](https://dev.to/sjh9714/i-audited-249-of-my-own-ai-coding-sessions-the-problem-wasnt-lying-4f42)

### Inferences
- A summary-faithfulness checker (NLI/LLM "is claim C entailed by context K") is structurally the same computation as report-vs-log if K is a serialised action log and claims are action claims ("ran the tests", "did not modify config"). Nobody has published that instantiation with an action-claim taxonomy; the closest is AgentLTL's regex entity-grounding predicate, which is per-task.
- The C2PA analogy is only partly transferable: C2PA binds *who made this asset*; the agent problem needs *did this narrative happen*, which requires interpretation of prose, not signatures.

### Gaps
- No paper was found that benchmarks summary-faithfulness models (e.g., NLI-based) on agent action reports; absence of evidence, but two search formulations returned nothing of the kind.
- No standards body draft for signed agent-action manifests (beyond OTel recording conventions and the TRACE spec listed in the awesome list) was located.

---

## Key question 5: Which survey/position paper names claim-level provenance / self-report verification as an open problem?

### Takeaway
The precise citation is **"From Agent Traces to Trust: A Survey of Evidence Tracing and Execution Provenance in LLM Agents", arXiv 2606.04990 (Wang et al., Griffith University et al.; v1 June 2026, v4 28 Jun 2026, v5 10 Sep 2026)**, whose abstract states that "Final-answer accuracy alone cannot explain how an output was produced, which evidence supported each claim, whether tool calls were justified" and whose §7.2 "Claim-Level and Semantic Provenance" names coarse-granularity attribution as an open challenge. It does *not* explicitly frame "verify the agent's narrative against its execution log" as a problem; the secondary candidate, "Auditable Agents" (arXiv 2604.05485, USC FORTIS Lab), gets closest with OP4 "Semantic policy decidability".

### Cited Findings
- Abstract (v5): "Final-answer accuracy alone cannot explain how an output was produced, which evidence supported each claim, whether tool calls were justified, how memory influenced later decisions, or where failures originated. This survey examines evidence tracing and execution provenance as foundations for process-level accountability" — [arXiv 2606.04990](https://arxiv.org/abs/2606.04990)
- Definitions: execution provenance = "the complete typed representation of an agent run, including evidence units, execution units such as retrieved documents, tool calls, parameters, observations, memory accesses, intermediate claims, actions, inter-agent messages, and final outputs, and their causal, procedural, dependency, update, contradiction, and invalidation relations"; evidence tracing = "the projection of this provenance structure onto evidence-support and influence relations between evidence units and agent claims, decisions, or actions" — [arXiv HTML v4](https://arxiv.org/html/2606.04990v4)
- §7.2 Claim-Level and Semantic Provenance: "Current attribution methods often operate at coarse granularity, such as answer-level citation, context-level faithfulness, or step-level logging." "A final response may be partially correct: one claim may be supported by retrieved evidence, another may be inferred beyond the source, and another may be contradicted by a tool output or memory item." "String-level matching and citation presence are therefore insufficient." — [arXiv HTML v4](https://arxiv.org/html/2606.04990v4)
- The fetched text contains "no explicit subsection addressing fabricated provenance, self-reported attribution, or evaluation without ground truth" — [arXiv HTML v4](https://arxiv.org/html/2606.04990v4)
- Auditable Agents (Nian, Yuan, Zhang, Li, Zhao; USC FORTIS; 7 Apr 2026): five dimensions — Action Recoverability, Lifecycle Coverage, Policy Checkability, Responsibility Attribution, Evidence Integrity; open problem OP4 "Semantic policy decidability: What classes of semantic policies … can be made mechanically decidable from the audit record?"; no passage on faithfulness of the agent's own account — [arXiv 2604.05485](https://arxiv.org/html/2604.05485v1)
- Other surveys in the awesome list's "Surveys and Foundations" section: "Visibility into AI Agents — Accountability-focused position on monitoring and logging"; "AgentOps: Enabling Observability of LLM Agents — Taxonomy of agent lifecycle artifacts and trace data"; "Survey on Evaluation of LLM-based Agents"; "A Survey on Trustworthy LLM Agents" — [awesome-auditable-ai §3](https://github.com/yzhao062/awesome-auditable-ai)

### Inferences
- If the proposal's phrase "the field's own survey" refers to a provenance/auditability survey, 2606.04990 is the one: it is the only 2026 survey specifically about evidence tracing and execution provenance, is listed in the awesome list under Audit Trails, and its §7.2 is the claim-granularity open problem. The honest reading is that it names *claim-level provenance* (fine-grained support for each claim) as open, and the *self-report-vs-log* framing is an inference from "a claim … may be contradicted by a tool output".
- Auditable Agents' "Policy Checkability" plus OP4 imply the same gap from the audit side: the record may be complete and intact, yet the semantic question "did the account match the record" is not mechanically decided by anything in their framework.

### Gaps
- I could not confirm which survey the proposal author had in mind; if it is not a provenance survey, "Visibility into AI Agents" or "AgentOps: Enabling Observability" (both in the awesome list) are the alternatives, and neither was fetched.
- v5 (10 Sep 2026) of 2606.04990 was not fetched in full; §7.2 quotes are from v4 (28 Jun 2026).

---

## Synthesis: what is and is not occupied (as of 2026-09-19)

### Takeaway
Everything up to and including "record everything, prove it wasn't tampered with, and show it to a person" is crowded. "Score the trajectory against a reference, a rubric, or a formal spec" is crowded. "Read the agent's prose report, decompose it into action claims, and test each claim against an independently recorded action log, with no per-task specification, emitting per-claim verdicts" is not occupied by any product, and is occupied in research only by narrow or unreleased instances.

### Cited Findings (occupancy map, each row pointing at the strongest occupant)
- Logging (harness transcript): Claude Code JSONL, Codex rollout JSONL, Copilot session logs, Entire, SpecStory, Compliance API — [Claude Code sessions](https://code.claude.com/docs/en/sessions); [openai/codex #3827](https://github.com/openai/codex/discussions/3827); [GitHub Copilot sessions](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/track-copilot-sessions); [entireio/cli](https://github.com/entireio/cli); [getspecstory](https://github.com/specstoryai/getspecstory); [Compliance sessions](https://platform.claude.com/docs/en/manage-claude/compliance-sessions)
- Logging (independent of harness): AgentSight eBPF, flightrec proxy/watcher/PATH-shim, Tracon, agent-trace probes — [agentsight](https://github.com/agent-sight/agentsight); [flightrec](https://github.com/cedric190703/flightrec); [tracon](https://github.com/mukes555/tracon); [agent-trace](https://github.com/ASSERT-KTH/agent-trace)
- Tamper-evidence: AgentLens, halo-record, MakerChecker, TRACE, YYLO custos-code — [agentlens](https://github.com/agentkitai/agentlens); [awesome-auditable-ai §7](https://github.com/yzhao062/awesome-auditable-ai); [yylo.dev](https://www.yylo.dev/)
- Show to a human: all flight recorders, Agent Flight Recorder ("read-only playback"), HANSEL (breadcrumbs) — [Agent-Flight-Recorder](https://github.com/OthmaneBlial/Agent-Flight-Recorder); [HANSEL](https://arxiv.org/abs/2606.18671)
- Score vs. reference trajectory: agentevals match modes; AgentLens PTA references — [agentevals](https://github.com/langchain-ai/agentevals); [arXiv 2605.12925](https://arxiv.org/abs/2605.12925)
- Score vs. rubric (LLM judge): Phoenix/Datadog/Galileo/Maxim/TruLens/Braintrust/Langfuse/Docent — see Q1/Q4 citations
- Verify vs. authored formal spec: AgentLTL (per-task FO-LTL from gold traces), AgentSpec, CPL, AgentGuard — [arXiv 2607.02599](https://arxiv.org/html/2607.02599); [awesome-auditable-ai §6](https://github.com/yzhao062/awesome-auditable-ai); [arXiv 2605.20923](https://arxiv.org/abs/2605.20923)
- Spec-free, class-level NL auditing of traces: Meerkat (corpus-level, safety/gaming), Arize Signal (production issue taxonomy), Laminar Signals (user-written plain-language rule) — [arXiv 2604.11806](https://arxiv.org/html/2604.11806); [Arize Signal](https://arize.com/blog/debug-production-ai-agents-with-signal-tutorial/); [Laminar Signals](https://laminar.sh/docs/signals/introduction)
- Report-vs-log, structured self-report: agent-trace (sim agent only; JSON trajectory vs OS probes; FAITHFUL/NOT FAITHFUL) and AER "fidelity score" (planned) — [agent-trace](https://github.com/ASSERT-KTH/agent-trace); [arXiv 2603.21692](https://arxiv.org/html/2603.21692)
- Report-vs-log, natural-language self-report, spec-free: **red-handed only**, restricted to "tests pass" claims, Claude Code transcript + git as evidence, deterministic, 4 stars — [red-handed](https://github.com/sjh9714/red-handed)
- Final-answer entity grounding vs tool outputs: AgentLTL κ_ground — but per-task spec — [arXiv 2607.02599](https://arxiv.org/html/2607.02599)

### Inferences
- **Occupied:** recording (both harness-level and kernel-level), integrity, human replay, reference/rubric/spec-based trajectory scoring, spec-free class-level trace auditing for safety and pathology, and one deterministic single-claim-type check for coding agents.
- **Not occupied:** a general checker that (a) takes the agent's natural-language final report as input, (b) extracts an open set of action-level claims (ran/verified/edited/did-not-touch/observed-output), (c) checks each against an action log recorded independently of the harness, (d) needs no per-task specification or reference trajectory, and (e) reports per-claim corroborated / unwitnessed / contradicted verdicts across harnesses. The pieces exist separately — agent-trace's taxonomy and probes, red-handed's claim patterns and two-evidence rule, AgentLTL's entity-grounding predicate, Laminar/Meerkat's class-level NL question — but no product or paper composes them, and the two research items nearest to it (agent-trace, AER) are respectively untested on real agents and unimplemented.
- The strongest counter-evidence to "this is needed" is red-handed's own base rate (0 confirmed false "tests pass" claims in 249 sessions, 7 unverifiable); the strongest supporting evidence is Meerkat's discovery of widespread benchmark gaming in which agents "verify" answers against the source that leaked them, and the DGM fabricated-test-log incident.

### Gaps
- Star counts and "last commit" dates were read off GitHub pages at fetch time and may be stale by days; treat as order-of-magnitude.
- Vendor evaluator inventories change frequently; Langfuse's and Maxim's template names were not enumerable from their docs pages.
- No systematic search of closed vendor changelogs (e.g., Braintrust Loop, Arize AX release notes) was done for a "claim verification" feature announced after the docs pages were written.
