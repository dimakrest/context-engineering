# Missions — long-running agent work with a contract

A **mission** is a multi-feature agent run whose definition of done is written **before any code
exists**. It is built entirely from Claude Code primitives already in this repo — skills, subagents,
files, and git. There is no framework, no daemon, and no new UI.

Adapted from Luke Alvoeiro's (Factory) multi-agent architecture. Design decisions are recorded
in the repository's issues.

---

**New to this? Start with the walkthrough:** `docs/MISSIONS_GETTING_STARTED.html` — open it in a
browser. This page is the reference; that one is the first-run guide.

---

## The idea in three sentences

Long agent runs don't drift because the model is weak. They drift because nothing forces "done" to be
defined before the code, and nothing forces the checker to be blind to the implementation. A mission
is those two constraints, plus enough bookkeeping to survive a context compaction.

---

## When to use it

| Use a mission | Use `/implement`, `/full-stack`, or just work |
|---|---|
| 3+ features, or work that outlives one context window | One sitting, one layer |
| Correctness is arguable and worth pinning down up front | The done-condition is obvious |
| Voice or UI **behavior** is part of "done" | A green suite is genuinely the goal |
| You want an independent agent to grade the result | You'll review it yourself in five minutes |

Missions have real overhead: planning tokens, validator runs, and — for conversational assertions —
possibly real spend on live systems. Don't spend it on a one-file change.

---

## Quick start

```
/missions:mission-plan     # interview, then write the contract. No product code is written here.
                  # → review .missions/<slug>/contract.md yourself before continuing

/missions:mission-crosscheck   # optional but cheap: an external reviewer derives the plan
                  # independently. Run it here -- a wrong assertion is the one thing
                  # nothing downstream can recover from.

/missions:mission-design   # architecture: exploration agents find the repo's existing patterns,
                  # design.md turns them into guidelines every worker is bound to
                  # → review .missions/<slug>/design.md yourself before continuing

/missions:mission-amend    # when the plan turns out to be wrong: a contract defect, a scope the
                  # user has narrowed, a design decision that reversed. Planning phase
                  # only -- and a contract amendment is not done until a crosscheck passes.

/missions:mission-run      # the loop: one worker at a time, blind validators at each milestone
/missions:mission-status   # an HTML page: coverage, spend, what's running, what's blocked
/missions:mission-resume   # after a compaction, a /clear, or a new day
```

**Read the contract before running.** It is the one artifact the rest of the machinery cannot recover
from being wrong about. Ten minutes there is worth more than anything you can do later.

---

## What it produces

```
.missions/<slug>/          # git-ignored run state
  mission.md               # goal, non-goals, constraints, model seats, budget cap
  contract.md              # assertions A001.., written before code, proof-class tagged
  design.md                # guidelines D001.. + pattern inventory, written before code
  features.md              # F001.. → milestone, assertions, procedures, status
  state.md                 # broadcast: every agent reads this first
  handoffs/F001.md         # what the worker did, what it left, commands + exit codes
  validation/              # per-milestone verdicts
  followups.md             # defects, as new features
  journal.jsonl            # append-only event log
```

Schema and templates: `${CLAUDE_PLUGIN_ROOT}/templates/MISSIONS_TEMPLATES.md`.

The mission's **terminal state is a branch plus a draft PR**. Never a merge. A human merges.

---

## The pieces

### Skills

| Skill | Does |
|---|---|
| `/missions:mission-plan` | Interviews you, argues about scope, emits `mission.md` + `contract.md` + `features.md`. Refuses to finish unless every assertion maps to a feature and every feature to an assertion. Writes **zero** product code. |
| `/missions:mission-design` | The mandatory architecture step between plan and run. Fans out read-only `mission-researcher` agents to find the repo's existing patterns, then writes `design.md` — guidelines `D001..` anchored to `file:line` exemplars. Workers are bound to them; blind reviewers grade conformance against them. Writes zero product code. |
| `/missions:mission-run` | The orchestrator loop. Refuses to dispatch without `design.md`. Dispatches one writing agent at a time, ingests handoffs, blocks progress on open issues, fires blind validators at milestones, halts on the triggers below. Ends by handing the finished branch to `/missions:mission-pr-review`. |
| `/missions:mission-pr-review` | The terminal whole-branch review, run in the `pr` phase. Opens the draft PR, runs `/simplify`, fires the general review and the repo's adversarial-review skill in parallel, assesses every finding with read-only agents, and writes an HTML findings report. Re-entrant via its progress file `validation/pr-review.md`. Fixes nothing — survivors go to `followups.md`. |
| `/missions:mission-crosscheck` | The cross-vendor blind review of the plan, run after `/missions:mission-plan` (contract mode) or after `/missions:mission-design` (design mode). Seals a spec package with our conclusions stripped, has an external reviewer derive the architecture independently, then **audits the transcript for contamination before any finding is read**. Routes contract defects to the user and never patches `contract.md`. Re-entrant via `crosscheck/progress.md`. |
| `/missions:mission-amend` | Changes a planned mission's contract, decomposition or scope without leaving half of it behind. Maps the blast radius before editing, applies edits that abort rather than half-apply, retires ids without renumbering, sweeps to zero live references, and gates on `check.sh` — a bidirectional coverage check, because the one-directional kind passes a mission whose two files disagree. **`planning` phase only**, and a contract amendment is not complete until `/missions:mission-crosscheck contract` passes on the result. |
| `/missions:mission-status` | Renders `.missions/<slug>/` into a self-contained HTML page — assertion coverage by proof class, features, spend vs cap, open issues. |
| `/missions:mission-resume` | Reconstructs position from disk and reconciles it against git. Git wins any disagreement. |

### Subagents

| Agent | Model (default seat) | Tools | Role |
|---|---|---|---|
| `mission-worker` | Sonnet 5 · per-feature `Seat:` override | Read/Write/Edit/Glob/Grep/Bash | One feature, clean context, one commit, one handoff. Never pushes. |
| `mission-researcher` | Sonnet 5 | read-only + web + named read-only graph tools (no wildcard: repowise's `get_answer` is an LLM call billed outside the caps, graphify's PR tools shell out to `gh`) | One bounded question, short cited answer. **The only agent run in parallel** — read-only is what makes that safe; MCP tools do not change its class. The planner's lookups and `/missions:mission-design`'s pattern exploration both use it. |
| `mission-reviewer` | Opus 5, `effort: xhigh` · `Reviewer seat:` override | read-only + Bash + named read-only graph / call-graph tools | Blind adversarial review. Gets the patch and the assertions; never the handoff, the worker's reasoning, or any tool that returns commit messages or PR bodies. Grades the patch's callers in an impact table. One per feature. |
| `mission-validator-scrutiny` | Sonnet 5 | Read/Bash | The repo's test layers, linters and type checkers, plus the code-health delta when repowise is indexed. Raw output and exit codes. Makes no repairs. |
| `mission-validator-behavior` | Opus 5 | Read/Bash + Playwright (+ any conversational test server the project exposes) | The QA engineer. Drives the real UI and the real conversational channel. Never reads the implementation. |

**Seats are executable, and measured.** The defaults above live in the agent definitions. A
mission records only deviations — `- **Seat:** opus` on a feature in `features.md`, `- Reviewer
seat: fable` in `mission.md` — `check.sh` validates them, the loop passes them as `model:` on the
Agent call, and the serial guard and journal hooks record the model that actually ran
(`journal-metrics.sh` sums dispatches and agent-hours per model). The reviewer's default stays
Opus until a mutation suite measures catch-rate per model; `fable` is offered per mission for
blast radius that includes auth, money or tenancy.

### Codebase intelligence — graphify and repowise

The plugin is project-agnostic, so it never assumes a code graph exists. `/missions:mission-plan`
probes once and writes one line under *Standing constraints* in `state.md` — `- Codebase
intelligence: graphify=cli+mcp (graphify-out/, <date>) · repowise=index (.repowise/)`, or `none` —
and every agent branches on that line from its digest.

| Consumer | Uses | Why |
|---|---|---|
| `mission-researcher` | `query_graph`, `get_neighbors`, `get_community`, `shortest_path`; `search_codebase`, `get_symbol`, `get_callers_callees`, `get_dependency_path` | orientation before reading — the graph says where to read, then the file is cited |
| `/missions:mission-plan`, `/missions:mission-design` | `graphify query`, `graphify god-nodes`; researchers asked for the community hub | size features to files; pick the exemplar the codebase converges on |
| `mission-reviewer` | `graphify affected "<symbol>"` (Bash), `get_neighbors`, `get_callers_callees`, `get_dependency_path`, `get_dead_code`, `get_health` | per-feature impact: every caller of a changed public symbol gets a verdict |
| `mission-validator-scrutiny` | `repowise health --format json` vs `baseline/health.json` | deterministic health delta — reported, never a gate |
| `/missions:mission-run` ingest | `graphify update .` after every handoff | AST-only, keeps the graph current for the next reviewer |
| `/missions:mission-pr-review` | `get_pr_impact`, `get_risk`, `graphify affected` across the branch | the cross-feature pass the blind reviewers cannot do |

**Blindness holds — by allowlist and by hook.** The reviewer's tool list is fixed by its definition
and deliberately omits `get_why` (git archaeology), `list_prs` / `get_pr_impact` / `triage_prs` (PR
bodies) and `get_answer` — they return the author's reasoning. The reviewer still holds Bash, so
`mission-shell-guard.sh` refuses `git log/show/diff/blame`, `gh`, `graphify prs` and handoff paths
from its shell (the harness tags every tool call inside a subagent with `agent_type`). **Spend
stays measured — by hook.** The same guard blocks, for every caller, `repowise generate`,
`repowise update` without `--index-only`, `repowise init` without `--no-prose` (or the legacy
`--index-only`), `graphify label/extract` and `cluster-only` without `--no-label`. Each can call
an LLM through the tool's own provider key, spend the dollar cap cannot see. Safe flags must
belong to each invocation; Repowise's `--full`, `--docs` and `--prose` overrides are blocked.
Use simple, literal commands for these operations; ambiguous shell forms are refused. The
researcher's allowlist is likewise named tools, never `mcp__repowise__*` — the wildcard would
enable `get_answer`.

Prerequisites, per project (the plugin documents them; it never edits your config):

```
uv tool install --with "mcp<2" "graphifyy[mcp,sql,terraform]"   # once; the MCP server needs [mcp], and 0.9.29 broke on mcp 2.x
claude mcp add -s local graphify -- $(cat graphify-out/.graphify_python) -m graphify.serve $PWD/graphify-out/graph.json
repowise init --no-prose --no-editor-setup --no-save-key .  # first run, Repowise 0.47.0; no LLM prose or editor setup
repowise update --index-only .                            # subsequent refreshes; requires an existing index
claude mcp add -s local repowise -- repowise mcp $PWD --transport stdio
```

For older Repowise versions, check `repowise init --help` and use `init --index-only` when
supported. `update --index-only` does not initialize a fresh repository.

Without them the agents behave exactly as before — the MCP tools are simply not there.

These five are **project-agnostic**. Everything they need to know about the repo they are working in
comes from the mission's `state.md`, written by the planner. A project's own agents still do their
jobs inside a mission — a `database` agent for migrations, engineering-standards agents for the
worker's conventions — but the mission machinery itself assumes nothing.

---

## The contract — the part that actually matters

An assertion states **observable behavior**, independent of implementation. The test: *could two
engineers satisfy it with completely different code, and would both be right?*

| ✗ Not an assertion | ✓ Assertion |
|---|---|
| `AlertService.reset_streak()` exists | After a tool call succeeds, that tool's failure streak is zero |
| Add a `status` column | A conversation created and never answered appears under "Waiting", not "Open" |
| Write tests for the parser | A malformed webhook payload is rejected with 422 and no row is written |

Every assertion carries a **proof class**, and the tag decides which validator runs and what the
milestone costs:

| Class | Proven by | Cost |
|---|---|---|
| `structural` | the repo's automated tests, at whichever layer fits | cheap |
| `conversational` | exercising the system through its real conversational channel — a call, a chat, an interactive session | slow, possibly real money |
| `interface` | Playwright over the real UI | slow |

`conversational` only applies to projects whose behavior *is* a conversation, and it is why the
workflow is worth the overhead on one. It's the class of defect a green suite confirms and never
catches: the code is right, the tests pass, and the system still says the wrong thing.

---

## Rules the loop enforces

1. **One writing agent at a time.** Read-only agents fan out; writers never do. Naive parallelism
   produces conflicts, duplicated work, and inconsistent architecture — the coordination cost eats
   the gain.
2. **The author never grades itself.** A worker's claim is an input; only a blind agent's evidence
   moves an assertion to `proven`.
3. **Progress blocks on open handoff issues.** Resolved, or explicitly deferred to `followups.md`,
   before the next feature starts.
4. **Validators never repair.** Defects become new features. A validator that fixes things puts
   ungraded code into the next milestone's diff.
5. **No push, no merge, no `--no-verify`, no `--admin`.**
6. **No migration and no write-SQL against a shared or remote database.** The specific hosts and
   rules for a given project go in that mission's `state.md`.

### Halts — BLOCK and ADVISORY

**BLOCK** (stop, decision card, wait): a cap reached — dollars, dispatches, wall-clock, repair
rounds · the convergence gate failed · a migration or any shared-DB write · a real side effect
outside the repo · root-cause classification says the *contract* is wrong (not merely that an
assertion failed twice) · `halt at every milestone` ceiling · anything needing a push or merge.

**ADVISORY** (journal the assumption, proceed, surface it next turn): everything else — first-pass
validation failure, "cannot tell", a design-conformance defect, a milestone boundary under the
default ceiling. The first full run idled ~85% of nine days, mostly waiting for "continue".

**Expect validation to fail on the first pass at every milestone.** That is the normal case and it is
what you're paying for — which is exactly why it is advisory, never a halt. A milestone that passes
everything first try is worth a second look at whether the assertions actually bite.

---

## Hooks — the deterministic layer

Prompts hold judgment; hooks hold non-negotiables. If a constraint can be checked by a 20-line bash
script, it lives here rather than in a prompt an agent may rationalize past at the moment it is most
inconvenient.

| Hook | Event · matcher | Enforces |
|---|---|---|
| `mission-serial-guard.sh` | PreToolUse · `Agent` | Admission control: one writer (`.writer`) and one executor (`.lease`) at a time, classed by the agent definition's tools (Write/Edit → writer, Bash → executor, anything else including MCP tools → static); blocks on open handoff issues, the `state.md` size cap, and the dollar / dispatch / wall-clock / repair-round caps. Takes the locks and journals `dispatch` (with the model that will run) + `session_cost` when it allows. Static agents are never blocked. |
| `mission-blind-review.sh` | PreToolUse · `Agent` | Validators stay blind — rejects a validator prompt containing handoff content, a diff handed to the behavior validator, or a reviewer told to run git instead of reading its patch file. |
| `mission-contract-first.sh` | PreToolUse · `Write\|Edit` | No product code while phase is `planning`. |
| `mission-commit-discipline.sh` | PreToolUse · `Bash` | No push outside phase `pr`, no merge, no `--no-verify`/`--admin`; feature id required in commit messages while implementing. |
| `mission-crosscheck-seal.sh` | PreToolUse · `Bash` | The crosscheck reviewer's spec package stays sealed — blocks a `codex` invocation referencing `.missions/` or `docs/plans/`. |
| `mission-shell-guard.sh` | PreToolUse · `Bash` | What an agent's own shell may not do. For the blind reviewer (identified by the harness's `agent_type`, else by a `.lease` held by `mission-reviewer` — the doc promises the field, nothing here has yet recorded it, so the lease is the fallback): no `git log/show/diff/blame`, no `gh`, no `graphify prs`, no handoff paths — `mission-blind-review.sh` polices the brief, this polices the shell. For every caller: no `repowise update`, no `repowise init` without `--index-only`, no `graphify label/extract`, no `cluster-only` without `--no-label` — they call an LLM through the tool's own key, outside every cap. |
| `mission-handoff-schema.sh` | not wired — the schema function | A worker's handoff has every section, records exit codes, and cites a commit that actually exists. Called by the driver after the worker exits and by `missions grade --self` before it does; no longer a `PostToolUse` hook (#4: grading at dispatch fired 29 false alarms). |
| `mission-journal.sh` | PostToolUse · `Agent` | Appends `agent_return` with the measured `duration_s` (joined to `dispatch` by tool id), agent id, the model that ran (the call's override, else the definition's default), status. Never fails a call. |
| `mission-release.sh` | PostToolUse / PostToolUseFailure · `Agent`, SubagentStop | Releases `.writer` / `.lease` when their holder returns, fails, or is stopped. |
| `mission-rehydrate.sh` | SessionStart · `startup\|resume\|compact` | Prints the mission digest as session context, so re-entry after a compaction starts from the rulebook and `resume_next`, not from line 1 of `state.md`. |

Shared helpers live in `${CLAUDE_PLUGIN_ROOT}/hooks/mission-lib.sh`. The scripts under
`${CLAUDE_PLUGIN_ROOT}/scripts/` — `mission-state.sh` (digest), `mission-archive.sh`,
`mission-patch.sh`, `mission-spend.sh`, `mission-converge.sh`, `check.sh`, `journal-metrics.sh`,
`lint-agents.sh` — are the loop's deterministic tools; the skills call them by path.
`lint-agents.sh` is the one aimed at the plugin itself: it parses `agents/*.md` the way the hooks
do and fails on a `model:` / `effort:` the harness rejects or a `tools:` list the awk would read as
empty (which default-denies the agent to *writer*). Run it before shipping an agent change.

**They are inert when no mission is running.** Installed as a plugin, these fire on `Agent`,
`Write`, `Edit`, and `Bash` calls in *every* session in *every* repo — so "no active mission" is
overwhelmingly the common case and must cost nothing. A mission is active while some
`.missions/*/state.md` in the current project has a phase other than `done` or `halted` (an
*unknown* phase counts as active, with a warning); with no `.missions/` directory, every repo
behaves exactly as it did before.

**Regression net:** `bash "${CLAUDE_PLUGIN_ROOT}/tests/run.sh"` runs every hook and script against
crafted stdin and fixture missions, inertness cases first. It is the replacement for the 99-case
suite that was dropped when the machinery moved to user level; add a case for every new block.

---

## Current status and honest limits

**Built:** eight skills, five subagents, nine hooks, eight scripts, the file schema, and a hook
regression suite (`tests/run.sh`), packaged as the `missions` plugin in the
`dimakrest/context-engineering` marketplace.

**v0.2 (2026-08-31)** wired the codebase intelligence (graphify / repowise) into the researcher,
reviewer, scrutiny validator and the loop, made model seats executable and journaled, and moved the
researcher and scrutiny seats from Haiku / Opus to Sonnet 5.

**v2 (2026-08-31)** applied the five fixes from the analytics-hour-filter retro: bounded assurance
(feature/file gate, proof budgets, finding registry, convergence gate); advisory vs blocking halts
with the decision card; hot/cold state (fenced machine block, digest, archive, size cap, rehydrate
on compaction); exact-range sealed review (patch files, no git for reviewers, host execution lease);
and measured spend (dollar / dispatch / wall-clock / repair-round caps from the harness's cost-state).

**Never run end to end.** No real mission has been *executed* with this. Planning has: one mission
has been planned, designed, crosschecked twice and amended twice, which exercised
`/missions:mission-plan`, `/missions:mission-design`, `/missions:mission-crosscheck` and `/missions:mission-amend` against a real
codebase. The loop itself — `/missions:mission-run`, the workers, the validators — has not run. Expect the
first execution to find gaps in the prompts, and treat it as the phase-0 dry run described in the
plan rather than as production work.

**Not validated — the validators.** Nothing has yet confirmed that `mission-reviewer` catches a
planted defect. Until a mutation suite exists (plant an off-by-one, a swapped tenant filter, a dropped
`await`; measure catch rate), treat validator verdicts as informative, not authoritative. Every
mechanism here increases confidence by construction, so an untested validator is an expensive machine
for generating green checkmarks.

**The vendor gap is now covered at plan time only.** The source architecture debiases by running
validation on a *different model provider*. `/missions:mission-crosscheck` supplies exactly that axis — and it
earns its keep: on a real contract it found an assertion describing a surface the codebase does not
have, which ten researchers and a full design pass had missed, and on a later run it found three
fresh defects in the amendment pass that was fixing the first batch.

But it covers the **plan**, not the **loop**. Everything downstream of `/missions:mission-design` — workers,
blind reviewers, milestone validators — is still Claude checking Claude, varying model, context
blindness and evidence independence but not vendor. That is most of what cross-provider validation
buys, since the dominant bias in practice is "the agent that wrote it believes it works". It is still
a weaker guarantee than the original, just in a narrower place than before.

**Unconfirmed.** Whether a Claude Code subagent can itself spawn subagents. The design assumes the
safe answer: all fan-out originates in the `/missions:mission-run` loop, never inside a validator.

**The driver (0.3, in progress).** Three runs showed that every long stall had one root: the thing
that should continue the mission was a model deciding whether to take another turn. `bin/missions`
replaces that with a program — a `while True:` that runs each worker as a blocking subprocess under
`claude -p` or `codex exec`, grades the handoff after the process exits, and stops only through a
typed reason. Today (D1 + D2 of #5) it drives the IMPLEMENT phase of a milestone and hands
VALIDATE back to `/missions:mission-run`. Grading happens once, after exit, keyed to the attempt
(#4): eight outcome classes, a watchdog that ends a run which committed and then went idle without
a handoff, reconstruction of that handoff from the commit, and `missions grade --self` so the
worker checks itself against the same function. Harness-agnostic enforcement (#13),
`resume`/`status` and the mutation tests (#6) follow. See the README's "Developing" section for the
commands and the trace tests.

---

## Related

- `${CLAUDE_PLUGIN_ROOT}/docs/MISSIONS_GETTING_STARTED.html` — the first-run walkthrough
- `${CLAUDE_PLUGIN_ROOT}/templates/MISSIONS_TEMPLATES.md` — file schema
- Whatever the current project documents about its own test layers, E2E setup and DB safety — the
  planner reads those and writes the relevant rules into the mission's `state.md`.
