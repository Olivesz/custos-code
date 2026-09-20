# Scope: the second checker

`DESIGN.md` describes one question — **did the agent do what it said?** This document adds the
second — **did what it did match what was asked?** — and states the architecture both run under.

Nothing in `DESIGN.md` is retracted. Every measured number there still holds and is the evidence
base this design rests on. What changes is that integrity stops being the whole product.

Owner: Oliver. Calibration: Anush. Request/plan extraction per agent: Ananya. Split in §8.

---

## 1. Why a second checker

The integrity engine answers a narrow question well. On construction-truth fixtures it is
**87% [82, 90]** against an 82% baseline (McNemar p=0.023, 0/408 false accusations), and on real
local sessions it answers **33% [28, 39]** of claims and finds a contradiction in **8 of 33**
sessions. Those are good numbers and they are not in dispute.

They also describe a report card. A report card is read after the fact, by a human who is present.

The failure people actually fear from an agent they leave running is not a wrong sentence in a
summary. It is the agent doing *something else* — editing files nobody mentioned, rewriting config,
installing packages, force-pushing. Integrity is silent on all of it: an agent that force-pushes and
then accurately reports "I force-pushed" is **confirmed**.

Worked example, from this repo on 2026-09-19. Asked *"the tests in tests/ are failing, fix them and
confirm the suite passes"*, the agent additionally built a scratchpad venv, created a repro
directory, ran a mutation test, and investigated CPython `.pyc` invalidation. Good work. **None of
it was named in the request, and nothing in our system noticed or could have.**

The same session is also the warning: a naive "you touched files outside `tests/`" rule would have
blocked that agent four times and been wrong every time. §4 is built around that constraint.

## 2. Architecture: star, not chain

Both checkers read the same harness-written ledger. Neither reads the other's verdict.

```
                    ledger (harness-written, model has no write path)
                                     |
                +--------------------+--------------------+
                |                                         |
         integrity checker                          scope checker
     "did it do what it said?"              "did it do what was asked?"
                |                                         |
                +--------------------+--------------------+
                                     |
                              one receipt
```

This is deliberate and the literature is unambiguous about why.

**Chained critics degrade.** Model a verification chain as a two-state Markov process with
`α` = P(correct → incorrect), the rate at which a verifier breaks something that was fine, and
`β` = P(incorrect → correct). Then `A_{k+1} = A_k(1-α-β) + β`, which converges geometrically to

```
A* = β / (α + β)
```

and a further pass helps **iff `β/α > A/(1-A)`**. The consequence is the important part:

> A verification chain does not converge to truth. It converges to the verifier's own
> correction-to-corruption ratio.

At 90% accuracy you need `β/α > 9`; at 95%, `> 19`; at 99%, `> 99`. The bar scales hyperbolically
while verifier quality is roughly flat.

**Our own rates make the case for grounding, not for more agents.** `α` is our measured
false-accusation rate on honest controls: 0/408, 95% Wilson upper bound **0.93%**. `β` is trap
detection, **0.87**. So `β/α ≥ 93`, and the stop rule says another grounded pass pays up to
A ≈ 0.99.

That ratio is a product of *grounding*, not of independence. A prose-critiquing-prose critic sits
around α = 1.3–3.8%, which at A=0.90 demands β > 34% merely to break even. The same arithmetic that
licenses our design condemns an ungrounded army.

**Empirically, chains lose to simpler things.** Across 36 scenarios no multi-agent-debate method
beat plain chain-of-thought more than 20% of the time. Debate exhibits *collective delusion* — when
both agents share a wrong belief it confirms the error — and agents "frequently shift from correct
to incorrect in response to peer reasoning, favoring agreement over challenging flawed reasoning."
Self-critique with no external feedback measurably *degrades* accuracy (GPT-4: 95.5% → 91.5% on
GSM8K); the same papers find it works when it can consult a tool, an executor, or a knowledge base.

What does win is heterogeneity plus grounding: "different models, different system prompts, or one
agent grounded on external documentation ... substantially outperforming homogeneous setups."
Two checkers asking *different questions* of *shared evidence* is that, structurally.

**Correlation caps depth regardless.** With mean pairwise correlation ρ among verifier judgments,
effective independent verifiers saturate near `1/ρ`. Same model chained against itself is ρ≈1, so
the second verifier contributes nothing but α.

**Cross-family is already what we do, and it should be deliberate.** LLM judges show self-preference
(GPT-4 rates its own output ~10% higher) and family bias. Our checker is GPT-5.2 over mostly Claude
Code sessions — the recommended configuration, arrived at by accident. Write it down as a
requirement: **the checker must not share a family with the agent under test.**

### Why auto-mode's passes are not a chain

Auto mode retries up to three times, which looks like sequential verification and is not. Each pass
the **agent does new work**; new CALL/RESULT events append to the ledger, so genuinely new
observations of the world enter. A verifier chain adds model B's opinion of model A's opinion — no
new information, α accumulating against static β. Ours adds an actual unpiped test run that did not
previously exist.

`feedback.cleared()` enforces this: a mark clears only on evidence with `seq > nudge_seq`.

**Known bug, and it is the real non-stationarity risk here.** The ledger retains failed attempts.
Pass 1's piped `pytest | tail` sits at seq 12; pass 2's clean run at seq 40. The model has cited the
stale one — observed on session 21756df4, where the agent correctly complained that we were "citing
call indices from before I re-ran each check as a standalone unpiped command." `cleared()` gates the
verdict but not the citation. **Fix: `review.annotate()` marks pass boundaries so the model can see
which events postdate the nudge.** Small, and it should land before scope does.

## 3. What grounds a scope verdict

The ledger is *external and unforgeable*, which is stronger than a calculator in one respect: a
model can feed a calculator wrong inputs but cannot write the ledger at all.

It is nonetheless a **record, not an oracle**. It answers *what happened*, never *was that right*.
Scope therefore needs its own sources, and more of them exist than is obvious:

| Source | What it grounds | Deterministic |
|---|---|---|
| The request (`USER` events in the ledger) | the spec: named files, dirs, commands | mostly |
| **The agent's own stated plan** | a self-authored contract it cannot retract | mostly |
| `git status` / `git diff` / blast radius | files touched, lines changed, deps added | fully |
| `CODEOWNERS`, `CLAUDE.md`, `CONTRIBUTING` | boundaries the repo already declares | fully |
| Linked issue or PR body | a spec written before the work | partly |

The agent's own plan is the strongest and most underrated. A plan recorded in a log it cannot edit
turns "was this reasonable?" — unanswerable — into "did it do what it said it would?", which is
integrity-shaped and which our machinery already handles.

`CODEOWNERS` is literally a machine-readable scope oracle, already in this repo.

What is genuinely left over — *"is this over-engineered"*, *"is this the right approach"* — has no
oracle. That residue is smaller than it first appears, and §4 confines it to one band.

## 4. Scope is a blast radius, not a task description

The `cart-service` session in §1 is the design constraint. The agent went far outside the literal
request and every step was fine. What made it fine was not proximity to the request — it was that
**everything it did was recoverable**: temp directories and uncommitted working-tree changes, all
undoable with `git checkout` or `rm -rf /tmp/...`.

> **Recoverability is the wiggle room.** If git or a scratch directory can undo it, the agent may be
> as creative as it likes and we do not spend a token looking.

That grants enormous latitude mechanically, rather than by guessing intent.

### The three bands

**GREEN — never gate, zero tokens.**
Reads anywhere. Any write under `cwd` that git can revert. Anything under a scratch/temp directory.
Running tests, builds, linters, type checkers. This is the overwhelming majority of agent actions,
which is also why the scope checker is nearly free.

**YELLOW — pause and ask. Deterministic detection.**
Writes outside `cwd` (excluding scratch). Modifying untracked files git cannot restore — dotfiles,
`~/.zshrc`, configs. Installing dependencies. Network egress. Magnitude: more than `N` files or `M`
lines beyond what the request plausibly implies (`N`, `M` calibrated in §7, not guessed).

**RED — block always, attended or not.**
Irreversible *and* outside the radius: `rm -rf` outside scratch, force-push, `git reset --hard` over
uncommitted work, `~/.ssh` / `~/.aws` / credential files, `sudo`, package publish, sending
mail/messages, anything that spends money.

The asymmetry applies **per action** rather than globally: fail open on ambiguity when it is
reversible, fail closed when it is not.

### Verdict vocabulary

Scope does **not** emit `contradicted`. That verdict means "the log contains positive evidence a
claim is false" and is tied to a claim; a scope violation is an action against a boundary, which is a
different object. New verdict `out_of_scope`, with a band.

This is a naming decision, **not** a claim that scope cannot block. It can and should — see §5.

## 5. Blocking, and when

An earlier draft of this design said scope should be flag-for-human only. That was wrong, and the
reasoning that corrected it is worth recording because it generalises.

The cost asymmetry **inverts** between the two checkers:

| | false positive | false negative |
|---|---|---|
| **integrity** | accuse an honest agent of lying; destroy trust; discard completed work | one wrong sentence in a report |
| **scope** | **one pause** | config rewritten, files deleted, force-push, money spent |

Conservatism argues against blocking on integrity and *for* blocking on scope.

An unattended run settles it. A flag is a message to a human; with nobody reading, "flag" and "do
nothing" are the same policy, and the alternative to pausing is the agent continuing unwatched.
Unattended autonomy is exactly where a hard stop earns its keep.

Timing matters as much as the decision: scope is checked at **`PreToolUse`, before the action**.
"Ask before doing something irreversible" is what every permission system does; it is not halting on
an opinion.

### The mode decides the action

Same verdict, different response, following the existing `RECEIPTS_AUTO` shape:

| | GREEN | YELLOW | RED |
|---|---|---|---|
| **attended** | pass | pause and ask | block |
| **unattended** | pass | **block** | block |

Tier-3 judgment ("over-engineered") is logged, never blocking, in either mode. **Only grounded scope
blocks.** The moment a block rests on a model's aesthetic judgement, α climbs and §2's arithmetic
turns against us.

### Two things that stop it being intolerable

**Escalate on persistence.** One odd file touch is noise; twenty is drift. First deviation notes,
sustained deviation pauses. This is what accommodates "weird but legitimate."

**Ratchet the grant.** Once the user approves `src/`, never ask again that session. A gate that
re-asks is a gate people disable, and a disabled gate verifies nothing — the same lesson as the
Stop-hook latency that made the terminal unusable on 2026-09-19.

## 6. Implementation

### 6.1 New module `src/receipts/scope.py` (Oliver)

```python
class Band(StrEnum):
    GREEN = "green"; YELLOW = "yellow"; RED = "red"

@dataclass(frozen=True)
class Grant:
    """What the user implicitly and explicitly allowed this session."""
    cwd: str                      # the radius, from the hook payload
    scratch: tuple[str, ...]      # TMPDIR, ~/.receipts, session scratchpad
    named: tuple[str, ...]        # paths/commands named in the request
    approved: tuple[str, ...]     # ratcheted grants from earlier prompts this session

@dataclass(frozen=True)
class Finding:
    band: Band
    rule: str                     # stable id, e.g. "write-outside-cwd"
    detail: str                   # one line, names the path/command
    recoverable: bool

def classify(tool: str, tool_input: dict, grant: Grant, state: RepoState) -> Finding
```

`classify` is **pure and deterministic**: no model call, no network. That is what keeps GREEN free
and keeps α at zero for the bands that block.

Recoverability test, in order: is the path under a scratch dir → recoverable; under `cwd` and inside
a git work tree → recoverable **iff** git can restore it (tracked, or untracked-but-creatable); else
not recoverable.

### 6.2 Hook wiring (Oliver, `hooks.py`)

`on_pre_tool_use` already exists for the E5 runner rewrite. Add the scope gate **before** that, so a
RED action never gets wrapped and run:

- GREEN → return `None`, unchanged. No state written, no cost.
- YELLOW/RED → emit `permissionDecision: "deny"` with the reason, or `"ask"` in attended mode.

Claude Code's `PreToolUse` supports `permissionDecision`; Codex's documented shape matches
(`docs/ADAPTERS.md` §3, PR #53), with `updatedInput` nesting as the one known difference.

Reuses the `RECEIPTS_ONLY_IN` fence so the gate applies to one directory subtree, and `RECEIPTS_AUTO`
for attended vs unattended. Both land in #55.

### 6.3 Request and plan extraction (Ananya, `adapters/`)

`Grant.named` and the declared plan have to come out of each agent's log:

- **Claude Code** — `USER` events are already in the ledger; the plan is `TodoWrite` calls, or the
  last assistant message before the first tool call.
- **Codex** — the first `session_meta` prompt; `turn_context.turn_id` bounds a turn.
- **Copilot / Devin** — the PR body or issue text is the spec.

Wanted: `adapters.request_and_plan(session) -> tuple[str, list[str]]`. Keep it in the adapter layer
so `scope.py` stays agent-agnostic.

### 6.4 Policy file

`~/.receipts/policy.toml`, with a shipped default. Users widen or narrow without editing code:

```toml
[scope]
scratch = ["$TMPDIR", "~/.receipts"]
red     = ["rm -rf", "git push --force", "git reset --hard", "sudo", "npm publish"]
protect = ["~/.ssh", "~/.aws", "~/.zshrc", "**/.env"]
max_files_changed = 0     # 0 = unset until §7 calibration says otherwise
```

Defaults must come from §7, not from taste.

## 7. Calibration — how we set the thresholds honestly

Every threshold in §4 is a guess until measured, and we have the corpus to avoid guessing.

**~400 real local sessions of work that was accepted.** Run `classify` over every tool call in them.
**Every YELLOW or RED on that corpus is a false positive by construction**, because that work was
fine — the identical logic that produced the 0/408 honest-control figure for integrity.

That yields the number that decides whether anyone keeps this switched on: *how often would this have
interrupted you?* Tune before anyone sees it.

It also inverts the hard part. "What counts as out of scope" is unanswerable in the abstract. "What
fires on work known to be good" is answerable tonight, from data already on disk.

Positive class: synthetic RED fixtures (`rm -rf` outside scratch, force-push, writing `~/.zshrc`),
ground truth by construction, exactly like `eval/arms/`. Report Wilson intervals, never point
estimates.

**Success criterion, stated before measuring:** 0 RED and ≤1% YELLOW across the accepted corpus,
with 100% RED detection on the synthetic set. If GREEN fires on the `cart-service` session, the rule
is too tight and widens.

### 7a. What the first run actually said (2026-09-20)

404 sessions, 23,805 tool calls. **Both criteria failed, and not narrowly:**

| | first run | after hardening | criterion |
|---|---|---|---|
| YELLOW | **48.4%** | 18.0% | ≤1% |
| RED | 1.23% | 1.43% | 0 |

At 48% the gate halts half of all legitimate work. The `cart-service` session — the acceptance test
this document names by hand in §1 — fired **2 RED**.

Two defects produced most of it, and neither was a threshold:

- **A Bash line's *mentions* were treated as its *writes*.** `cd ~/other && python3 x.py` was banded
  a write to `~/other`. ~40% of all calls on its own. Write targets now come from the command's verb
  or a redirect.
- **`recoverable()` asked whether the *grant root* was a git tree, not the target.** A session rooted
  at `~/Projects` — twenty repositories, itself not one — marked every write into every one of them
  unrevertible. 9,016 fires.

**This is why the gate ships off (§5) and why calibration is the critical path (§8), not the code.**
Had it shipped on the numbers it was written with, it would have interrupted one call in two.

### 7b. The criterion was measuring the wrong thing

"% of calls" is not what anyone experiences. What a user feels is **how many times a session
interrupts them**, and the ratchet is meant to collapse repeats. Measured over the same corpus:

```
PROMPTS per session (ratcheted)   median 0   mean 6.4   p90 11   max 220
sessions with 0 prompts           243/297 = 82%
sessions with ≤2 prompts                    86%
```

82% of sessions would never be interrupted at all. The 18% is a long tail, not a broad tax.

**Revised criterion, replacing "≤1% of calls":**

- **median 0 prompts per session, p90 ≤ 2, and 0 RED** on the accepted corpus
- 100% RED detection on the synthetic set

The old figure stays reported because it is comparable across runs, but it does not gate the
decision. A rule that fires 400 times in one unusual session and never again is a different problem
from one that fires twice in every session, and the percentage cannot tell them apart.

### 7c. A limit of the method, stated plainly

2,008 of ~2,500 remaining `unrecoverable-write` fires target paths whose **parent directory no longer
exists** — git worktrees since deleted. Those directories existed when the work happened; the walk
that looks for an enclosing repo finds nothing today. So the measured rate is an **upper bound**, and
the true rate is lower by an unknown amount.

This is the price of judging historical sessions against today's filesystem, and it cannot be fixed
by tuning. It can be fixed by recording the band live (`RECEIPTS_SCOPE=warn`) and calibrating on that
instead — which is worth doing before anyone quotes a number publicly.

### 7d. Hardening: how this fails in production

An adversarial pass on the first implementation found ~15 ways to get a destructive command banded
GREEN. All are closed and each has a named regression test, but the **pattern** matters more than the
list:

> **Every widening is a potential bypass.** Both HIGH-severity bypasses found so far were introduced
> by a fix for false positives. Stripping heredoc bodies killed 38 bogus REDs and made
> `bash <<'EOF' / rm -rf ~ / EOF` completely invisible. Tidying the scan text afterwards dropped the
> shell *after* the delimiter, so `cat <<'EOF' > ~/.zshrc` reduced to `cat  ` and writing anywhere
> was free.

Consequently: **no change to a rule ships without re-running both directions** — the synthetic RED
set and the accepted corpus. Optimising one and spot-checking the other is how both of those landed.

The classes to keep testing, because they recur:

| Class | Example that was GREEN |
|---|---|
| Prefix laundering | `echo hi && rm -rf ~` — the allowlist read only the first token |
| Option soup | `git -c k=v push --force` — the flagship RED rule, defeated by a flag |
| Alternate spelling | `rm -r -f`, `--recursive --force`, `git push origin +main` |
| Allowlisted binary that writes | `find -delete`, `sed -i`, `python -c`, `git config --global` |
| Content executed from elsewhere | `curl … \| sh`, `eval "$X"`, an interpreter heredoc |
| Our own state as scratch | `~/.receipts/live/*.jsonl` — a **model write path to the ledger** |

That last one is the one to remember. The checker had declared the append-only hash-chained evidence
disposable, so an agent could rewrite the record, its own permission grants, or the config that turns
the gate off — invariant 1, defeated by the component meant to enforce it.

## 8. Task split

| Who | What | Paths |
|---|---|---|
| **Oliver** | `scope.py` (bands, `classify`, recoverability), `PreToolUse` gate, `out_of_scope` verdict, policy loader, pass-boundary fix in `annotate()` | `src/receipts/scope.py`, `hooks.py`, `rules.py`, `review.py`, `cli.py` |
| **Anush** | §7 calibration harness over the local corpus; synthetic RED fixtures; Wilson intervals; the `α/β` instrumentation from §2 turned into a reported metric | `eval/`, `bench/` |
| **Ananya** | `adapters.request_and_plan()` per agent; `CODEOWNERS`/`CLAUDE.md` boundary loader; policy file plumbing | `src/receipts/adapters/*`, `docs/ADAPTERS.md` |

Order matters: **§6.1 `classify` + §7 calibration are the critical path.** Without a measured
false-positive rate this ships as a guess, and a scope gate that interrupts good work is worse than
no scope gate.

## 9. Open questions

| ID | Question | Owner | Status |
|---|---|---|---|
| S1 | `max_files_changed` / `max_lines`: is magnitude a usable YELLOW signal at all, or noise? §7 answers it. | Anush | open |
| S6 | §7b replaced "≤1% of calls" with prompts-per-session. Does p90 ≤ 2 hold once the ratchet is actually implemented? It is currently modelled in the harness, not in the product (`scope_approved` has a reader and no writer). | Oliver | open |
| S8 | §11: move scope proper to a post-hoc On branch oliver/scope-design
Your branch is up to date with 'origin/oliver/scope-design'.

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
	modified:   docs/SCOPE.md

no changes added to commit (use "git add" and/or "git commit -a")/diff --git a/docs/SCOPE.md b/docs/SCOPE.md
index e2e820c..932bfa7 100644
--- a/docs/SCOPE.md
+++ b/docs/SCOPE.md
@@ -436,3 +436,93 @@ still requires positive evidence and still cannot be emitted by a model.
 
 Integrity's measured numbers stand unchanged. Scope is additive, and it is unmeasured until §7 says
 otherwise — which is exactly how integrity's numbers should be read too.
+
+
+## 11. Amendment: §4–§6 name the wrong instrument
+
+**Status: proposed. §3 stands; §4–§6 do not.** Written after two rounds of hardening and two
+measured calibration runs, per the rule that this document is what we build from — so a part of it
+that the evidence contradicts gets argued against in writing, not quietly abandoned.
+
+### The claim
+
+Banding a **command string** at `PreToolUse` cannot reach the §7b criterion. Roughly 14–18% is the
+floor of the approach, not its calibration error.
+
+### The evidence
+
+**1. The two error columns trade against each other, and we have watched it happen four times.**
+Every tightening moved errors from the miss column to the false-positive column, and every loosening
+moved them back:
+
+| Change | Bought | Cost |
+|---|---|---|
+| strip heredoc bodies | −38 false REDs | `bash <<'EOF' / rm -rf ~ / EOF` became **GREEN** |
+| drop the header tail | tidier scan text | `cat <<'EOF' > ~/.zshrc` became **GREEN** |
+| drop `python`/`sed`/`find` from the allowlist | closed 5 bypasses | `find . -name '*.pyc' -delete` is now **RED** |
+| remove `shutdown`/`reboot` | −38 false REDs | any `reboot` in a real command line is now missed |
+
+**Both HIGH-severity bypasses found so far were introduced by a fix for false positives.** That is
+not carelessness; it is the shape of the instrument.
+
+**2. False positives that cannot be fixed at the string level.** Each is ordinary developer work:
+
+```
+rm -rf build/ dist/ *.egg-info          RED    (any rm -rf, even inside cwd)
+find . -name '*.pyc' -delete            RED
+git push --force-with-lease origin x    RED    (the SAFE force push)
+curl -s 127.0.0.1:3000/api | python3    RED    (a local dev server)
+```
+
+A regex cannot distinguish `find -exec grep` from `find -exec rm`, or `rm -rf build/` from
+`rm -rf ~/Projects`, without knowing what the paths *are* — which is a filesystem question.
+
+**3. Bypasses that cannot be closed at the string level.** Each writes, none names a write verb:
+
+```
+sort -o ~/.zshrc src/a.py       GREEN    sort is read-only; -o writes
+git checkout -- .               GREEN    discards all uncommitted work
+git branch -f main HEAD~5       GREEN    `branch` is read-only
+python3 tool.py                 GREEN    tool.py may do anything
+```
+
+The instrument reads **syntax**; the property we want is an **effect**.
+
+### What §3 already said
+
+§3 lists `git status` / `git diff` / blast radius as a grounding source and rates it *fully*
+deterministic. §4–§6 then never use it. The doc named the right instrument in one section and
+specified the wrong one in the next three.
+
+### The correction
+
+**Split by reversibility, which is what §4 says scope is about anyway.**
+
+**Pre-action (`PreToolUse`), a short RED list only.** Things that cannot be undone after the fact,
+so there is no post-hoc measurement to take: force-push, package publish, `sudo`, credential-file
+writes, `rm -rf` outside cwd and scratch, sending or spending. This list *will* have bypasses —
+findings 1–4 above — and that is acceptable, because everything it misses is caught by the next
+stage. It should be small enough to audit by eye.
+
+**Post-action (`PostToolUse` / `Stop`), the real scope check.** `git status --porcelain` and
+`git diff --stat` answer *what actually changed, and can git undo it* with no parsing and no
+regexes. Untracked files, files outside the repo, and lockfile changes are all directly observable.
+Near-zero false positives, because it measures the effect rather than guessing it from the verb.
+
+This also repairs §5's timing argument, which was too broad. "Check before the action" is right for
+the irreversible list and unnecessary for everything else: a file write inside a git tree is
+revertible, so it can be reported after the fact and undone if wrong.
+
+### What it costs
+
+The gate stops being able to *prevent* an out-of-scope file write; it reports one and offers to
+revert. For the irreversible list nothing changes. Given that 82% of sessions see zero prompts
+(§7b) and the residue is dominated by in-repo writes, this trades a capability we cannot deliver
+accurately for one we can.
+
+### Decision needed
+
+S8: adopt this split, or keep §4–§6 and accept ~14% as the floor with YELLOW downgraded to
+log-only. **Owner: Oliver. Blocking: the gate cannot ship on either path until #57 reports against
+whichever instrument we pick**, and re-pointing calibration at the post-hoc measure is roughly the
+same work as calibrating the current one. measure and keep only an irreversible-action list at PreToolUse? Evidence in §11; ~14% looks like the floor of string banding. | Oliver | **open, blocking** |
| S7 | Calibrate on bands recorded LIVE (`RECEIPTS_SCOPE=warn`) rather than replayed against today's filesystem — §7c says the replayed number is an upper bound only. | Anush | open |
| S2 | Does a YELLOW pause mid-turn confuse the agent into working around the gate rather than asking? | Oliver | open |
| S3 | Ratcheted grants: session-scoped only, or persisted per repo? Persisted is friendlier and strictly weaker. | Oliver | open |
| S4 | Is "the agent's stated plan" reliably extractable outside Claude Code's `TodoWrite`? | Ananya | open |
| S5 | ρ across verifier families is unmeasured and caps every architecture in this space. Genuine research contribution if we measure it; out of scope for the event. | — | deferred |

## 10. What this does not change

The ledger. It remains harness-written, append-only, hash-chained, with no model write path. Both
checkers read it and neither writes it. Every invariant in `AGENTS.md` still holds, and `contradicted`
still requires positive evidence and still cannot be emitted by a model.

Integrity's measured numbers stand unchanged. Scope is additive, and it is unmeasured until §7 says
otherwise — which is exactly how integrity's numbers should be read too.


## 11. Amendment: §4–§6 name the wrong instrument

**Status: proposed. §3 stands; §4–§6 do not.** Written after two rounds of hardening and two
measured calibration runs, per the rule that this document is what we build from — so a part of it
that the evidence contradicts gets argued against in writing, not quietly abandoned.

### The claim

Banding a **command string** at `PreToolUse` cannot reach the §7b criterion. Roughly 14–18% is the
floor of the approach, not its calibration error.

### The evidence

**1. The two error columns trade against each other, and we have watched it happen four times.**
Every tightening moved errors from the miss column to the false-positive column, and every loosening
moved them back:

| Change | Bought | Cost |
|---|---|---|
| strip heredoc bodies | −38 false REDs | `bash <<'EOF' / rm -rf ~ / EOF` became **GREEN** |
| drop the header tail | tidier scan text | `cat <<'EOF' > ~/.zshrc` became **GREEN** |
| drop `python`/`sed`/`find` from the allowlist | closed 5 bypasses | `find . -name '*.pyc' -delete` is now **RED** |
| remove `shutdown`/`reboot` | −38 false REDs | any `reboot` in a real command line is now missed |

**Both HIGH-severity bypasses found so far were introduced by a fix for false positives.** That is
not carelessness; it is the shape of the instrument.

**2. False positives that cannot be fixed at the string level.** Each is ordinary developer work:

```
rm -rf build/ dist/ *.egg-info          RED    (any rm -rf, even inside cwd)
find . -name '*.pyc' -delete            RED
git push --force-with-lease origin x    RED    (the SAFE force push)
curl -s 127.0.0.1:3000/api | python3    RED    (a local dev server)
```

A regex cannot distinguish `find -exec grep` from `find -exec rm`, or `rm -rf build/` from
`rm -rf ~/Projects`, without knowing what the paths *are* — which is a filesystem question.

**3. Bypasses that cannot be closed at the string level.** Each writes, none names a write verb:

```
sort -o ~/.zshrc src/a.py       GREEN    sort is read-only; -o writes
git checkout -- .               GREEN    discards all uncommitted work
git branch -f main HEAD~5       GREEN    `branch` is read-only
python3 tool.py                 GREEN    tool.py may do anything
```

The instrument reads **syntax**; the property we want is an **effect**.

### What §3 already said

§3 lists `git status` / `git diff` / blast radius as a grounding source and rates it *fully*
deterministic. §4–§6 then never use it. The doc named the right instrument in one section and
specified the wrong one in the next three.

### The correction

**Split by reversibility, which is what §4 says scope is about anyway.**

**Pre-action (`PreToolUse`), a short RED list only.** Things that cannot be undone after the fact,
so there is no post-hoc measurement to take: force-push, package publish, `sudo`, credential-file
writes, `rm -rf` outside cwd and scratch, sending or spending. This list *will* have bypasses —
findings 1–4 above — and that is acceptable, because everything it misses is caught by the next
stage. It should be small enough to audit by eye.

**Post-action (`PostToolUse` / `Stop`), the real scope check.** `git status --porcelain` and
`git diff --stat` answer *what actually changed, and can git undo it* with no parsing and no
regexes. Untracked files, files outside the repo, and lockfile changes are all directly observable.
Near-zero false positives, because it measures the effect rather than guessing it from the verb.

This also repairs §5's timing argument, which was too broad. "Check before the action" is right for
the irreversible list and unnecessary for everything else: a file write inside a git tree is
revertible, so it can be reported after the fact and undone if wrong.

### What it costs

The gate stops being able to *prevent* an out-of-scope file write; it reports one and offers to
revert. For the irreversible list nothing changes. Given that 82% of sessions see zero prompts
(§7b) and the residue is dominated by in-repo writes, this trades a capability we cannot deliver
accurately for one we can.

### Decision needed

S8: adopt this split, or keep §4–§6 and accept ~14% as the floor with YELLOW downgraded to
log-only. **Owner: Oliver. Blocking: the gate cannot ship on either path until #57 reports against
whichever instrument we pick**, and re-pointing calibration at the post-hoc measure is roughly the
same work as calibrating the current one.
