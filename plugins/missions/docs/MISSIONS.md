# Missions — long-running agent work with a contract

A **mission** is a multi-feature agent run whose definition of done is written **before any code
exists**. Claude Code and Codex share its skills, agent instructions, files and Git workflow.
Codex executes the implementation loop through the bundled driver. See the
[installation guide](../README.md#install) and [runtime guide](RUNTIMES.md) for host-specific
commands and enforcement; the workflow reference below uses Claude command names.

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

## Two ways to run a mission

**Planning is identical either way.** `/missions:mission-plan` and `/missions:mission-design` write
the same five files under `.missions/<slug>/`, and both runners read them. What differs is *what
drives the loop* once the plan exists.

| | **The session loop** — `/missions:mission-run` | **The driver** — `bin/missions run` |
|---|---|---|
| What decides the next action | a Claude Code session following the skill | a Python program (stdlib only, ≥ 3.9) |
| A worker is | a `mission-worker` subagent inside that session | a separate OS process — `claude -p` or `codex exec` |
| Grading a handoff | the orchestrator model reads it and judges | deterministic: the schema function plus `git`, after the process exits |
| The loop's own token cost | real, every turn — reload, ingest, bookkeeping | none; no model call happens outside a dispatch |
| Surviving a compaction | `/missions:mission-resume` | nothing to survive — state is re-read from disk each iteration |
| Caps | the hooks enforce them around each dispatch | checked in code before every paid dispatch |
| How it ends | prose, plus `resume_next` | a typed exit code (0–130), plus `resume_next` |
| Harness | Claude | Claude **or** codex, per mission |
| Terminal steps (the `pr` phase) | **yes** — draft PR and whole-branch review | **not yet** (#30): it stops and hands the branch back |

**Use the session loop** when you want to watch it, intervene, or take a mission all the way to a
reviewed draft PR. **Use the driver** for long unattended stretches, for a dollar cap enforced
before every dispatch, or when you want a grade that does not depend on a model's judgment.

They are not exclusive. Both read and write the same `.missions/<slug>/`, so a mission can move
between them mid-flight — and today it has to, because the driver stops at the `pr` phase.

> **If you have been calling these v1 and v2** — yes, these are them. The docs use the descriptive
> names because `v2` already means two other things in this project: the 2026-08-31 rework below,
> and the fenced `mission-state` block that replaced the prose header in `state.md` (the driver
> refuses a mission without it — *"the driver needs the v2 block"*).

---

## Quick start

### 1. Plan — the same either way

```
/missions:mission-prd      # optional, and the right place to start from a requirements document:
                  # write a PRD, or audit an existing one, into .missions/<slug>/prd.md so the
                  # planner lifts the assertions instead of inventing them

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
```

**Read the contract before running.** It is the one artifact the rest of the machinery cannot recover
from being wrong about. Ten minutes there is worth more than anything you can do later.

### 2a. Run it in a session

```
/missions:mission-run      # the loop: one worker at a time, blind validators at each milestone
/missions:mission-status   # an HTML page: coverage, spend, what's running, what's blocked
/missions:mission-resume   # after a compaction, a /clear, or a new day
```

The session is the orchestrator: it reloads the digest each turn, dispatches one worker, ingests the
handoff, and decides again. It stops on a BLOCK halt (below) and proceeds on an advisory one. The
terminal steps happen here too — `phase: pr`, then `/missions:mission-pr-review`, then `phase: done`.

### 2b. Run it with the driver

```bash
git checkout -b mission/<slug>          # the branch state.md names; the driver never creates one
echo .missions/ >> .git/info/exclude    # preflight warns when .missions/ is tracked

M=plugins/missions/bin/missions         # or put bin/ on your PATH

$M init      .missions/<slug> --harness claude   # writes driver.json.        Costs nothing.
$M preflight .missions/<slug>                    # refuses a bad setup.       Costs nothing.
$M run       .missions/<slug> --dry-run          # the queue and the real argv. Costs nothing.
$M run       .missions/<slug> --limit 1          # the first paid dispatch: one worker
$M run       .missions/<slug>                    # let it go
```

Run it **from the checkout, on the mission branch** — preflight refuses a detached HEAD or a branch
other than the one `state.md` names, because the worker's git hooks reject commits off it. Then read
the exit code:

| Exit | Reason | What it wants |
|---|---|---|
| `0` | done | nothing — the last milestone closed |
| `1` | error | read the run directory it names |
| `2` | preflight-failed | fix the setup; nothing was dispatched |
| `3` | limit-reached | nothing — `--limit` / `--until` did what you asked |
| `4` | budget | a cap raise in `mission.md`, journaled with the reason — or stop here |
| `5` | gate-blocked | a decision: unblock a feature, amend the plan, reconcile the branch |
| `6` | authority | an action the driver has no authority to take |
| `7` | contract | the contract is wrong; `/missions:mission-amend` |
| `8` | provider-quota | wait for the reset, then run again |
| `130` | interrupted | check the active feature against git, then run again |

Re-running is always safe: a closed milestone is never re-entered, and a validation round resumes at
the step it did not reach. When it stops with `gate-blocked` at `phase: pr`, finish in a session —
`/missions:mission-pr-review`.

Full command reference, the codex sandbox note, and how grading works:
`${CLAUDE_PLUGIN_ROOT}/README.md`.

---

## What it produces

```
.missions/<slug>/          # git-ignored run state
  prd.md                   # optional: product intent, from /missions:mission-prd. Frozen at plan
                           #   time -- once contract.md exists, the contract governs
  mission.md               # goal, non-goals, constraints, model seats, budget cap
  contract.md              # assertions A001.., written before code, proof-class tagged
  design.md                # guidelines D001.. + pattern inventory, written before code
  features.md              # F001.. → milestone, assertions, procedures, status
  state.md                 # broadcast: every agent reads this first
  handoffs/F001.md         # what the worker did, what it left, commands + exit codes
  validation/              # per-milestone verdicts
  followups.md             # defects, as new features
  journal.jsonl            # append-only event log

  # the driver adds these, and nothing else:
  driver.json              # harness, per-role deadline + dollar cap, sandbox, env passthrough
  runs/<task>/             # the prompt it sent, the harness log, outcome.json with the grade
  githooks/                # the git hooks the worker's process runs under
  .driver.lock             # one driver per mission directory
```

Every other file keeps the shape the skills wrote it in, whichever runner produced it.

Schema and templates: `${CLAUDE_PLUGIN_ROOT}/templates/MISSIONS_TEMPLATES.md`.

The mission's **terminal state is a branch plus a draft PR**. Never a merge. A human merges.

---

## The pieces

### Skills

| Skill | Does |
|---|---|
| `/missions:mission-prd` | Optional, before `/missions:mission-plan`. Writes a PRD, or audits an existing one, so the planner and the design step can read it literally: behavioural acceptance rows with fail-safe pairs, release metrics fenced off from implementation scope, no stale line anchors, superseded proposals marked as such, and the decisions deliberately left to the mission interview named. Chooses the slug and writes `.missions/<slug>/prd.md` — the one mission file that exists before the plan, and the only thing it writes. Zero product code. |
| `/missions:mission-plan` | Interviews you, argues about scope, emits `mission.md` + `contract.md` + `features.md`. Refuses to finish unless every assertion maps to a feature and every feature to an assertion. Writes **zero** product code. |
| `/missions:mission-design` | The mandatory architecture step between plan and run. Fans out read-only `mission-researcher` agents to find the repo's existing patterns, then writes `design.md` — guidelines `D001..` anchored to `file:line` exemplars. Workers are bound to them; blind reviewers grade conformance against them. Writes zero product code. |
| `/missions:mission-run` | The orchestrator loop. Refuses to dispatch without `design.md`. Dispatches one writing agent at a time, ingests handoffs, blocks progress on open issues, fires blind validators at milestones, halts on the triggers below. Ends by handing the finished branch to `/missions:mission-pr-review`. |
| `/missions:mission-pr-review` | The terminal whole-branch review, run in the `pr` phase. Opens the draft PR, runs `/simplify`, fires the general review and the repo's adversarial-review skill in parallel, assesses every finding with read-only agents, and writes an HTML findings report. Re-entrant via its progress file `validation/pr-review.md`. Fixes nothing — survivors go to `followups.md`. |
| `/missions:mission-crosscheck` | The cross-vendor blind review of the plan, run after `/missions:mission-plan` (contract mode) or after `/missions:mission-design` (design mode). Seals a spec package with our conclusions stripped, has an external reviewer derive the architecture independently, then **audits the transcript for contamination before any finding is read**. Routes contract defects to the user and never patches `contract.md`. Re-entrant via `crosscheck/progress.md`. |
| `/missions:mission-amend` | Changes a planned mission's contract, decomposition or scope without leaving half of it behind. Maps the blast radius before editing, applies edits that abort rather than half-apply, retires ids without renumbering, sweeps to zero live references, and gates on `check.sh` — a bidirectional coverage check, because the one-directional kind passes a mission whose two files disagree. **`planning` phase only**, and a contract amendment is not complete until `/missions:mission-crosscheck contract` passes on the result. |
| `/missions:mission-status` | Renders `.missions/<slug>/` into a self-contained HTML page — assertion coverage by proof class, features, spend vs cap, open issues. |
| `/missions:mission-resume` | Reconstructs position from disk and reconciles it against git. Git wins any disagreement. |

### The driver

| Runner | Is |
|---|---|
| `bin/missions` | The out-of-process alternative to `/missions:mission-run`: a stdlib-only Python program (≥ 3.9) that owns the loop instead of a session deciding whether to take another turn. `init` · `preflight` · `run` · `grade`. It runs each worker and each validator as a blocking subprocess under `claude -p` or `codex exec`, grades what they left behind after the process exits, and stops only through a typed exit code. It writes `driver.json`, `runs/<task>/`, `githooks/` and `.driver.lock` into the mission directory and nothing else new — every other file keeps the shape the skills wrote it in, and the hooks keep working alongside it. See *Two ways to run a mission* above. |

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

**Both runners enforce all six.** In a session they are the skill's invariants, backed by the hooks;
in the driver they are code paths the loop cannot step around.

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

**In the driver these are exit codes.** A BLOCK halt is `budget` (4), `gate-blocked` (5) or
`contract` (7) and sets `phase: halted`; an advisory one is journaled and the loop continues. Either
way `resume_next` in `state.md` says where it stood, and re-running is safe.

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
`tests/traces/run.sh` drives the real driver over a temporary repo with a stub worker, and
`tests/mutants.sh` breaks one driver rule at a time and asserts that the trace defending it fails
— a suite that stays green under a mutant is not testing what it claims to. `tests/harness/run.sh
claude|codex|both` is the paid live smoke; the suites never run it.

---

## Current status and honest limits

**Built:** nine skills, five subagents, nine hooks, eight scripts, the file schema, the
out-of-process driver (`bin/missions`), and five test layers — hooks and scripts (`tests/run.sh`),
driver traces (`tests/traces/run.sh`), driver mutants (`tests/mutants.sh`), driver unit tests
(`tests/driver-selftest.py`) and a paid live smoke (`tests/harness/run.sh`, never run by the
suites). Packaged as the `missions` plugin in the `dimakrest/context-engineering` marketplace.

**v0.2 (2026-08-31)** wired the codebase intelligence (graphify / repowise) into the researcher,
reviewer, scrutiny validator and the loop, made model seats executable and journaled, and moved the
researcher and scrutiny seats from Haiku / Opus to Sonnet 5.

**The 2026-08-31 rework** applied the five fixes from the analytics-hour-filter retro: bounded assurance
(feature/file gate, proof budgets, finding registry, convergence gate); advisory vs blocking halts
with the decision card; hot/cold state (fenced machine block, digest, archive, size cap, rehydrate
on compaction); exact-range sealed review (patch files, no git for reviewers, host execution lease);
and measured spend (dollar / dispatch / wall-clock / repair-round caps from the harness's cost-state).

**Never run end to end on real work — either runner.** Planning has: one mission has been planned,
designed, crosschecked twice and amended twice, which exercised `/missions:mission-plan`,
`/missions:mission-design`, `/missions:mission-crosscheck` and `/missions:mission-amend` against a
real codebase. The loop itself — the workers, the validators — has not run a real mission under
`/missions:mission-run` or under the driver. What *has* run under the driver is a fixture repo with
one real worker (`tests/harness/run.sh`, the paid smoke). Expect the first real execution to find
gaps in the prompts, and treat it as the phase-0 dry run described in the plan rather than as
production work.

**Not validated — the validators.** Nothing has yet confirmed that `mission-reviewer` catches a
planted defect. Until a *validator* mutation suite exists (plant an off-by-one, a swapped tenant filter, a
dropped `await`; measure catch rate — `tests/mutants.sh` is a different thing, it mutates the
driver), treat validator verdicts as informative, not authoritative. Every
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

**The driver (0.3).** Three runs showed that every long stall had one root: the thing that should
continue the mission was a model deciding whether to take another turn. `bin/missions` replaces that
with a program. *Two ways to run a mission* above is how to use it; the plugin README is how it
works.

*What it does:* drives a mission from its first feature to its last milestone's close — IMPLEMENT
with the grade taken once, after the worker's process exits and keyed to the attempt (#4: nine
outcome classes, the watchdog, handoff reconstruction, `missions grade --self`); triage of the open
issues a handoff raises; and VALIDATE — scrutiny, a blind review per feature, behavior validation
where the contract needs it, negotiate, `proven` written only from validator verdicts, converge,
archive, the next milestone. Follow-ups and repair features are registered from what a judgment step
proposes and the driver applies. Enforcement no longer depends on the harness (#13), and each layer
claims only what it holds: a run's environment is built from a whitelist, so no credential reaches a
worker through it (no token, no agent socket, no askpass, an empty credential helper, an empty `gh`
config — what `HOME` holds stays readable); git hooks scoped to that environment refuse a
cooperating worker, and `--no-verify` bypasses them; the post-exit grade is the gate that does not
depend on the worker at all.

*Still open:* the terminal steps and the push (the `pr` phase, #30), `status` (#19), and `resume`
with sleep-and-resume on a provider quota (#7).

*How far it is proven:* `tests/traces/run.sh` drives the real driver over a temporary repo with a
stub worker; `tests/mutants.sh` breaks one driver rule at a time and asserts that the trace
defending it fails; `tests/harness/run.sh claude|codex|both` is the paid live smoke with one real
worker. Under the **claude** adapter that smoke has completed a feature end to end. **No codex
worker has** — the codex adapter dispatches, grades and stops correctly, but treat it as plumbing
that is tested rather than a worker that is proven. On Linux `codex exec` also sandboxes each
command with bubblewrap, which needs an unprivileged user namespace; where the host refuses one,
`init` warns and `preflight` refuses before anything is spent, and both name
`adapters.codex.sandbox: "danger-full-access"` as the operator's opt-out for a host that is already
a sandbox.

---

## Related

- `${CLAUDE_PLUGIN_ROOT}/docs/MISSIONS_GETTING_STARTED.html` — the first-run walkthrough
- `${CLAUDE_PLUGIN_ROOT}/templates/MISSIONS_TEMPLATES.md` — file schema
- Whatever the current project documents about its own test layers, E2E setup and DB safety — the
  planner reads those and writes the relevant rules into the mission's `state.md`.
