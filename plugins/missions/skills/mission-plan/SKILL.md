---
name: mission-plan
description: Plan a mission - a multi-feature agent run whose definition of done is written before any code. Interviews the user, then emits mission.md, contract.md and features.md under .missions/<slug>/. Use when a task is too big for one session, when the user says "mission", "/missions:mission-plan", or before /missions:mission-run. Writes zero product code.
user_invocable: true
---

# /missions:mission-plan — write the contract before the code

You are planning a **mission**: a multi-feature run executed later by `/missions:mission-run`. Your entire
output is three files. **You write no product code in this skill.** Not a stub, not a scaffold, not
"just the model file". If you write product code here, the contract becomes a description of what you
already built, which is the exact failure this whole workflow exists to prevent.

> Tests written after the implementation don't catch bugs. They confirm decisions.
> The contract exists so that "correct" is defined by someone who hasn't seen the code yet.

## When to use this

| Use a mission | Just do the work instead |
|---|---|
| 3+ features, or work that outlives one context window | Single-layer change, one sitting |
| Correctness is arguable and worth pinning down up front | Obvious done-condition |
| Behavior a test suite can't see is part of "done" | Pure refactor with a green suite as the goal |
| You want a blind reviewer to grade the result | You'll review it yourself immediately |

If the task is small, say so and stop. A mission has real overhead — planning tokens, validator runs,
possibly real spend on live systems. Don't spend it on a one-file change.

## Step 0 — read before planning

1. The repo's own documentation for the affected area — `CLAUDE.md`, a wiki index, `docs/`.
   If the project has a docs-first rule, it applies here.
2. The actual code for the seams you intend to change — enough to size features honestly.
3. **The project's own rules** — test layers, database safety, git discipline, review process.
   You are going to write these into the mission's `state.md`, because the agents that execute the
   mission are project-agnostic and `state.md` is the only place they learn what this repo requires.

Dispatch `mission-researcher` agents (Agent tool, `subagent_type: mission-researcher`) for bounded
lookups; they are read-only and cheap, and you may run several at once. Do not read the whole codebase
yourself.

**Probe the codebase intelligence once, here — the agents never guess at it.**

```bash
test -f graphify-out/graph.json && echo "graphify=cli$(claude mcp get graphify >/dev/null 2>&1 && echo +mcp)"
test -d .repowise && echo "repowise=index$(claude mcp get repowise >/dev/null 2>&1 && echo +mcp)"
true   # a missing index is an answer, not a failed step
```

Write the result as one line under *Standing constraints* in `state.md`, where the digest carries
it to every agent: `- Codebase intelligence: graphify=cli+mcp (graphify-out/, <date>) ·
repowise=index (.repowise/)` — or `none`. When graphify exists, start your own lookups with
`graphify query "<term>"` and `graphify god-nodes` (seconds, no LLM): a community listing is the
fastest honest way to size a feature to the files it touches. When repowise is indexed, capture the
baseline the scrutiny validator diffs against: `mkdir -p .missions/<slug>/baseline && repowise
health --format json 2>/dev/null | sed -n '/^[{[]/,$p' > .missions/<slug>/baseline/health.json`
(repowise prints its log lines on stdout ahead of the JSON; the `sed` keeps only the document —
verify the file parses). Use `repowise update --index-only` only for an existing index. Initial
setup uses `repowise init --no-prose --no-editor-setup --no-save-key` on 0.47.0; older versions
may support `init --index-only` instead (check `init --help`). Never run paid `repowise generate`
or combine safe forms with `--full`, `--docs` or `--prose`: their spend is outside the caps.
The guard checks safe flags per invocation; use simple, literal commands.

## Step 1 — interview the user, and argue

You are a sounding board, not a stenographer. Before writing anything, resolve:

- **Goal in one sentence**, plus explicit **non-goals**. The non-goals matter more; they're what stops
  a worker from "improving" something at 2am.
- **Blast radius** — does this touch migrations, authorization boundaries, money, data belonging to
  other people, or anything with an irreversible side effect outside the repo?
- **What "done" looks like to a user**, not to a developer.
- **The autonomy ceiling** — `advisory` (default: the loop proceeds under stated assumptions and
  surfaces them at the next human turn; only caps, shared-DB writes, external side effects, a wrong
  contract or a push stop it) or `halt at every milestone`. Never offer "halt on validation
  failure": first-pass failure is the normal case, and wiring it as a halt pages the human at every
  milestone by design — the last mission lost ~126 h that way.
- **Budget** — four hard limits the guard enforces: a **dollar cap** (measured from the harness),
  a **dispatch cap** (worker + reviewer + validator dispatches; the one governor that would have
  stopped the last mission's 26-feature pinning run on its own), an **active wall-clock cap**, and
  **repair rounds per assertion** (default 2). Plus the behaviour-validation live-run cap and a
  terminal-review reserve. Tokens are informational. Missions without numbers get "informational
  only" warnings and no enforcement.

- **Seats** — the defaults live in the agent definitions (worker Sonnet; reviewer Opus at `xhigh`;
  researcher and scrutiny Sonnet; behavior Opus) and need no line. Record only deviations, and
  they are executable: a per-feature `- **Seat:** opus` in `features.md` for a genuinely gnarly
  feature (an unfamiliar library, binary output, a security boundary), and `- Reviewer seat: fable`
  in `mission.md` when the blast radius includes auth, money or tenancy. `check.sh` validates both;
  the loop passes them as `model:` on the Agent call; the journal records what actually ran.

Push back on scope. A mission that is 12 features long is usually two missions.

## Step 2 — write the validation contract

This is the artifact. Everything else is bookkeeping.

An assertion states **observable behavior**, independent of implementation. The test for a good
assertion: *could two engineers satisfy it with completely different code, and would both be right?*

| ✗ Not an assertion | ✓ Assertion |
|---|---|
| `AlertService.reset_streak()` exists | After a tool call succeeds, that tool's failure streak is zero |
| Add a `status` column | A conversation created and never answered appears under the "Waiting" tab, not "Open" |
| Refactor the aggregator | A caller who interrupts mid-sentence hears the bot stop within one turn and answer the new question |
| Write tests for the parser | A malformed webhook payload is rejected with 422 and no row is written |

Rules the contract must obey:

- **Behavioral, not structural.** No assertion may name a function, class, or column that doesn't
  already exist.
- **Independently checkable.** Each one names a way to observe it — an endpoint, a call, a screen.
- **Negative cases included.** For every "it works", write the "it refuses / it fails safe" pair.
  Adversarial validators need something to bite on.
- **Isolation is never implied.** If the feature touches data scoped to a user, tenant, or account,
  write the cross-boundary leakage assertion explicitly.
- Number them `A001`, `A002`, … and never renumber. Add `A0nn` at the end if you discover more.

### Proof class — tag every assertion

The tag decides which validator runs, how long a milestone takes, and what it costs in real money.

| Class | Proven by | Validator | Cost |
|---|---|---|---|
| `structural` | The repo's automated tests, at whichever layer fits | `mission-validator-scrutiny` | cheap, fast |
| `conversational` | Exercising the system through its real conversational channel — a call, a chat, an interactive session | `mission-validator-behavior` | slow, possibly real spend |
| `interface` | Playwright driving the actual UI | `mission-validator-behavior` | slow |

If an assertion has no proof class, it isn't an assertion yet — it's a wish. Rewrite it or cut it.

Aim for the majority `structural`. Reserve `conversational` for behavior a green test suite genuinely
cannot catch — what the system *says*, when it hands off, how it handles interruption — and only for
projects that have such a channel at all. It is the expensive class, so be deliberate.

If the project has a skill for drafting a testing strategy, run it on the plan doc once the features
exist; it knows the repo's test layering and will sharpen how each `structural` assertion gets proven.

## Step 3 — decompose into features

A feature is **one coherent, independently testable slice** — one worker, one clean context window.
Target **2–5 features per milestone**. Findings with the same root cause are repaired together;
test-only and prose-only fixes do not get a feature unless they independently move a release gate.
If a worker would need to read twenty files to start, it's two features; if two features touch the
same three files, it's one. The last mission cut 56 features over 23 files (one test file touched
37 times) and the environment monetised every one of them — `check.sh` now rejects a feature list
whose count exceeds the distinct files it touches.

Every assertion also gets a **proof budget** in the contract (min evidence that closes it; max
pinning effort it may consume — default one pinning feature). The floor stops inert tests; the
ceiling stops 26 consecutive test-only features.

For each feature record:
- `F00n` id and a one-line title
- The **assertions it satisfies** (ids)
- The **files** it will touch (`- **Files:**` — the feature/file gate reads this)
- The **milestone** it belongs to
- **Procedures** — the specific repo rules that apply: which test layer, who handles a migration,
  which docs get updated. Be concrete; the worker knows nothing about this project except what you
  write down here and in `state.md`.
- Dependencies on other features (the loop runs them in order)
- A **seat** (`- **Seat:** opus`) only when the feature warrants a model above the worker's default

Milestones group features into something a validator can exercise as a coherent whole. Two to five
features per milestone is the useful range.

## Step 4 — coverage check (this is a gate, not a formality)

Refuse to finish unless all six hold:

1. Every assertion appears in at least one feature.
2. Every feature lists at least one assertion. A feature satisfying nothing is scope creep — cut it.
3. The union of all features' assertions equals the contract exactly. Print the diff if not.
4. Every assertion has a proof class **and a proof budget**.
5. The feature count does not exceed the distinct files the features touch.
6. Every milestone that introduces an `interface` or `conversational` assertion also budgets the
   validator run that proves it (the loop cannot close such a milestone on structural proof alone).

Then run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/check.sh" .missions/<slug>` after writing the files —
it checks 1–5 mechanically. If you can't close a gap, the contract is incomplete or the decomposition
is wrong. Say which, and fix it before handing over.

## Step 5 — emit the files

Create `.missions/<slug>/` (git-ignored) with `mission.md`, `contract.md`, `features.md`, `state.md`
(fenced `mission-state` block first — `phase: planning`, `resume_next`, `state_cap_lines: 200`),
an empty `handoffs/`, `validation/`, `patches/`, an empty `followups.md`, and an empty `journal.jsonl`.
Keep the standing-constraints section under ~1.5 KB by referencing the repo's own rules by path;
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/mission-state.sh" .missions/<slug>` must print without
complaint before you hand over — it is what every agent will be briefed with.
Templates: `${CLAUDE_PLUGIN_ROOT}/templates/MISSIONS_TEMPLATES.md`.

`state.md` is where the project's own rules go — test layers, database safety, git discipline, the
review process. The worker, reviewer and validator agents are project-agnostic by design; `state.md`
is the only channel through which they learn what this repo requires. A vague `state.md` produces a
worker that guesses.

Also write the human-facing plan where this repo keeps plans (`docs/plans/<slug>-plan.md` is a good
default), and **copy the contract into it** — a reviewer must be able to see what "done" was defined
as without reading the run state. Never commit a plan doc unless the user explicitly asks.

## Step 6 — hand over

Report to the user, tightly:
- assertion count by proof class
- feature and milestone count
- the budget cap and autonomy ceiling you recorded
- the project rules you wrote into `state.md`, so the user can correct them before a worker obeys them
- **the two or three assertions you are least sure about** — the ones most likely to be wrong. That
  list is worth more than the summary, because a wrong contract is the one failure mode the rest of
  the machinery cannot recover from.

Then stop.

**Offer `/missions:mission-crosscheck` before `/missions:mission-design`.** It is optional, it is cheap, and this is
the moment it is worth most: an external reviewer attacks the contract without seeing our reasoning,
and a wrong assertion caught here costs an edit instead of a design pass built on a false premise.
The first real run found an assertion describing an API surface that does not exist — after the
design pass had already been written against it.

The next required step is `/missions:mission-design` — the mandatory architecture pass that turns the repo's
existing patterns into guidelines the workers are bound to — and only then `/missions:mission-run`. Each is a
separate, explicit invocation by the user.
