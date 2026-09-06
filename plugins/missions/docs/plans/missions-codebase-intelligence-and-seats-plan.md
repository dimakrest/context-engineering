# Missions v0.2 — codebase intelligence (graphify / repowise) and model seats

**Status:** proposed · **Target:** `plugins/missions` (bump to 0.2.0) · **Author:** Claude + Dima · **Date:** 2026-08-31

## 1. Why

Two things the plugin does not do today, both verified on disk:

1. **Its agents never use the codebase intelligence that already exists.** The host repo carries a 66 MB
   graphify graph and a `CLAUDE.md` rule that mandates `graphify query` before grepping; the
   plugin's agents grep. `mission-researcher` — the one agent whose whole job is orientation —
   has `Read/Glob/Grep/WebFetch/WebSearch` (`agents/mission-researcher.md:5-10`) and **cannot run
   `graphify query` at all** (no Bash, and giving it Bash would make it an *executor* under
   `hooks/mission-lib.sh:232`, which kills the parallel fan-out that is its reason to exist).
2. **The model seats are a table nobody reads.** `mission.md` says "Sonnet 5 (Opus for gnarly
   features)" but the dispatch template (`skills/mission-run/SKILL.md:100-125`) passes only
   `subagent_type` + `prompt` — no `model:` — so every worker runs on the frontmatter default.
   The journal never records which model ran (`hooks/mission-journal.sh:63-72`, contradicting
   `docs/MISSIONS.md`'s "agent id, model, status"), so nothing can measure a seat choice.

## 2. Findings that constrain the design

| # | Finding | Evidence |
|---|---|---|
| F1 | Serial guard classes an agent *static* iff its `tools:` has none of `Write/Edit/NotebookEdit/MultiEdit/*/Bash`. **MCP tools do not change the class.** | `hooks/mission-lib.sh:220-234` |
| F2 | Subagent frontmatter supports `model: sonnet\|opus\|haiku\|fable\|inherit`, `effort: low..max`, and `tools: mcp__<server>__*`. `mcpServers`, `hooks`, `permissionMode` are **ignored for plugin subagents** — MCP servers must be configured in the user/project config, not in the agent file. | code.claude.com/docs/en/sub-agents (fetched 2026-08-31) |
| F3 | graphify 0.9.29: `graphify query` is a 1.5 s CLI, no LLM, substring+IDF matching (noisy without vocab expansion); `graphify affected "X"` is a reverse-traversal impact query; `graphify update <path>` is AST-only; an MCP server exists — `python -m graphify.serve graphify-out/graph.json` — exposing `query_graph, get_node, get_neighbors, get_community, god_nodes, graph_stats, shortest_path, list_prs, get_pr_impact, triage_prs`. | `graphify --help`; `graphify/serve.py:1195-1310`; `~/.claude/skills/graphify/references/exports.md:59-65` |
| F4 | repowise 0.12.0: MCP server with 16 tools. **Index-only (no LLM):** `search_codebase, get_symbol, get_callers_callees, get_dependency_path, get_execution_flows, get_community, get_overview, get_architecture_diagram, get_dead_code, get_health, get_risk, get_context, get_graph_metrics, get_why` (verified after review: no `provider.generate` call in `tool_risk.py` / `tool_context.py`). **LLM-backed:** `get_answer, annotate_file`. `repowise health` is deterministic (CCN, nesting, brain-method) with `--format json --module <prefix> --trend`. `repowise init --index-only` builds the index with zero LLM spend. | `repowise/server/mcp_server/tool_*.py` (llm refs counted); `repowise health --help`; `repowise init --help` |
| F5 | **repowise is currently dead on this machine.** The user-level MCP entry points at `<host-repo-v6>`, which does not exist; the host repo has no `.repowise/` (`repowise status` → 0 pages); the global `repowise-augment` PostToolUse hook exits 0 silently without an index. | `~/.claude/settings.json:104,278-286`; `repowise status` in the host repo |
| F6 | graphify is live in three checkouts of the host repo (`graphify-out/` present) but is **not exposed as an MCP server** in any config. | `.mcp.json`, `~/.claude.json` |
| F7 | Blindness leak surface: `get_why` (git archaeology → commit messages), `list_prs/get_pr_impact/triage_prs` (PR bodies) return exactly the author reasoning the blind reviewer must not see. | `tool_why.py` (`_run_git_log`), `serve.py:1272-1310` |
| F8 | Pricing (claude-api skill cache 2026-06-24, $/1M in/out): Fable 5 10/50 · Opus 5 5/25 · Sonnet 5 2/10 · Haiku 4.5 1/5 (200 K context, prior generation). Effort `xhigh` is the Claude Code default and "the best setting for most coding and agentic use cases" on Opus 5 / Sonnet 5; `low` is recommended for subagents doing simple tasks. | claude-api skill, "Current Models" + "Thinking & Effort" |
| F9 | Reviewer catch-rate is **unmeasured** — the docs already say a mutation suite is the missing gate. Any model change to the reviewer is a guess until that exists. | `docs/MISSIONS.md` § Honest limits |

## 3. Decisions

### Codebase intelligence

**D1 — Discovery is a plan-time fact, not an agent-time guess.** `/missions:mission-plan` probes once
and writes one line under *Standing constraints* in `state.md` (so it reaches the ≤2 KB digest
every agent is briefed with):

```
- Codebase intelligence: graphify=cli+mcp (graphify-out/, 2026-08-31) · repowise=index (.repowise/) | none
```

Probe: `test -f graphify-out/graph.json`; `test -d .repowise`; `claude mcp list` for `graphify` /
`repowise`. Agents branch on this line; the plugin stays project-agnostic and every use below is
guarded by it.

**D2 — `mission-researcher` is the primary consumer.** It gets `mcp__graphify__*` and
`mcp__repowise__*` in its `tools:` allowlist and a rewritten "How to answer": *orient with the graph
first (`query_graph` / `get_community` / `search_codebase`), then read the cited files, then cite.*
It stays **static** (F1) so the fan-out is untouched. If the servers are not configured the tools
simply do not exist and it behaves as today.

**D3 — `mission-reviewer` gets impact tools, minus the reasoning leaks.** Allowlist:
`mcp__graphify__query_graph, get_node, get_neighbors, shortest_path`,
`mcp__repowise__get_symbol, get_callers_callees, get_dependency_path, get_dead_code, get_health`.
**Never** `get_why`, `list_prs`, `get_pr_impact`, `triage_prs`, `get_answer` (F7). Its prompt
template in `mission-run` adds one step: *for every public symbol the patch changes, run
`graphify affected "<symbol>"` (Bash — it already holds the lease) and check the callers you find
against the assertions; report unreviewed callers as "cannot tell".* This is the blind, per-feature
impact analysis.

**D4 — `mission-validator-scrutiny` reports a health delta.** The planner captures a baseline
(`repowise health --format json > .missions/<slug>/baseline/health.json`) when repowise is indexed.
Scrutiny reruns it for `--module` of each touched path and reports the delta as one more raw
output. A worsened score is a finding routed through the normal negotiation — **advisory, never a
gate**; the convergence gate stays as is.

**D5 — The loop keeps the graph current.** Handoff ingest (`mission-run` § Ingesting a handoff)
gains step 4: `graphify update . 2>&1 | tail -3` when `graphify-out/` exists. AST-only, seconds,
no LLM — and it is what the host repo's own `CLAUDE.md` requires after code changes. `repowise update` is
**not** added (it can generate wiki pages through an LLM; spend must stay measured).

**D6 — `mission-pr-review` gets the whole-branch impact pass.** It may use
`get_pr_impact` / `triage_prs` / `get_risk` — it is not blind by design and runs in phase `pr`.

**D7 — Environment prerequisites are documented, not automated.** A new *Codebase intelligence*
section in `docs/MISSIONS.md` with the two `claude mcp add` lines (project scope) and
`repowise init --index-only`. Fixing the dead `<host-repo-v6>` entry in `~/.claude/settings.json` is a
separate, user-approved step — the plugin never edits user config.

### Model seats

| Seat | Today | Proposed | `effort` | Why |
|---|---|---|---|---|
| Orchestrator | session model | `inherit` (unchanged) | session | the human's choice |
| `mission-researcher` | haiku | **sonnet** | default (unset) | 1 M context (Haiku 4.5 is 200 K — one large module read overflows it); citation reliability is the whole contract ("cite or say you're unsure"); its cost is input-dominated and output is 3-15 lines, so the delta is $1→$2 per M input on a tiny spend; and it feeds `design.md`, the most leveraged artifact in the run (F8). Decision 2026-08-31: no `effort:` override — default effort |
| `mission-worker` | sonnet | sonnet, **per-feature seat override** | default (unset) | keep; make the "Opus for gnarly features" line executable (D8) |
| `mission-validator-scrutiny` | opus | **sonnet** | default (unset) | it runs commands and reports exit codes; no judgment is asked of it; 2.5× cheaper per token (F8). Decision 2026-08-31: default effort |
| `mission-reviewer` | opus | opus, **`effort: xhigh` explicit**, seat-overridable to `fable` | xhigh | catch-rate is the value; `xhigh` is the documented best setting for review-shaped work and pins it even when the session runs lower. **Default stays Opus until the mutation suite exists (F9)** — `fable` is offered per mission for blast radius that includes auth / money / tenancy |
| `mission-validator-behavior` | opus | opus (unchanged) | high | drives real UI / calls; judgment matters; unchanged |

**D8 — Seats become executable.** Three small pieces:
1. `features.md` accepts an optional `- **Seat:** opus|sonnet|fable|haiku` line per feature;
   `mission.md`'s seats table gains an optional `Reviewer seat:` value. `check.sh` tolerates both.
2. The dispatch templates in `mission-run` pass `model: <seat>` when a seat is set, otherwise
   omit it (frontmatter default wins).
3. The journal records the model: `tool_input.model` when overridden, else the agent definition's
   `model:` (new `mission_agent_model()` in `mission-lib.sh`), on `dispatch` and `agent_return`.
   `journal-metrics.sh` gains a per-model spend line so a seat choice can finally be measured.

### Explicit non-changes

- No new agents. The planner, design, run and pr-review skills stay the callers; the five agents
  stay the workers.
- No hook edits for blindness — the MCP allowlist in the reviewer's frontmatter is the mechanism,
  and `mission-blind-review.sh` already rejects git commands in prompts. A test asserts the
  allowlist (T3).
- No `repowise-augment` integration; it is a session-level hook, not a mission concern.

## 4. Changes by file

| File | Change |
|---|---|
| `agents/mission-researcher.md` | `model: sonnet`, `effort: low`; add `mcp__graphify__*`, `mcp__repowise__*` to `tools`; rewrite "How to answer" (graph → read → cite); add "if the graph tools are absent, fall back to docs → grep as today" |
| `agents/mission-reviewer.md` | `effort: xhigh`; add the D3 allowlist (named tools only); add an "Impact" section to *What to return* (callers found via `graphify affected`, verdict per caller) |
| `agents/mission-validator-scrutiny.md` | `model: sonnet`, `effort: low`; add the health-delta step guarded by the state.md line |
| `agents/mission-worker.md` | no model change; add "do not run `graphify update` — the loop does" (avoid double work / lease contention) |
| `skills/mission-plan/SKILL.md` | Step 0 probe (D1) + baseline capture (D4); seats table gets `Reviewer seat:`; researcher briefing says "orient with the graph" |
| `skills/mission-design/SKILL.md` | researcher questions ask for `get_community` / `god_nodes` context so exemplars are chosen from the hub, not the first grep hit |
| `skills/mission-run/SKILL.md` | dispatch templates pass `model:` from the seat (D8); ingest step 4 `graphify update` (D5); reviewer prompt gains the `graphify affected` step (D3) |
| `skills/mission-pr-review/SKILL.md` | whole-branch impact via `get_pr_impact` / `get_risk` when available (D6) |
| `hooks/mission-lib.sh` | `mission_agent_model()` (frontmatter `model:` reader) |
| `hooks/mission-serial-guard.sh`, `hooks/mission-journal.sh` | journal `model` (D8.3) |
| `scripts/check.sh` | accept optional `Seat:` line / `Reviewer seat:` value |
| `scripts/journal-metrics.sh` | per-model dispatch count + duration |
| `templates/MISSIONS_TEMPLATES.md` | seats table update; `Seat:` line; codebase-intelligence line; `baseline/` dir |
| `docs/MISSIONS.md` | fix the journal claim; new *Codebase intelligence* section (D7); seats table |
| `tests/gen-cases.py` | cases T1-T8 below |
| `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` | 0.1.0 → 0.2.0 |

## 5. Acceptance checks (what the post-change reviewers grade against)

- **A1** A `mission-researcher` whose `tools:` include `mcp__graphify__*` and `mcp__repowise__*` is classed `static` by `mission_agent_class` and is never blocked by the serial guard.
- **A2** `mission-reviewer`'s `tools:` name no tool from {`get_why`, `list_prs`, `get_pr_impact`, `triage_prs`, `get_answer`} and no wildcard `mcp__*__*`.
- **A3** A dispatch with `tool_input.model = "opus"` journals `"model":"opus"`; one without journals the frontmatter model (`"model":"sonnet"` for the worker).
- **A4** `check.sh` passes a `features.md` with and without `- **Seat:**` lines, and fails on an unknown seat value.
- **A5** `mission-state.sh` digest includes the codebase-intelligence line and stays ≤ 2048 bytes on the fixture mission.
- **A6** In a repo with no `.missions/`, every hook is byte-for-byte inert (existing inertness cases still pass).
- **A7** No hook, script, skill or agent references a graphify/repowise tool without the `state.md` guard or an existence test — `grep -n "graphify\|repowise"` across the plugin returns only guarded uses and docs.
- **A8** `docs/MISSIONS.md` no longer claims the journal records the model *before* the code does; after, the claim is true.
- **A9** `bash tests/run.sh` is green.
- **A10** Frontmatter `model:` values are only `sonnet|opus|haiku|fable|inherit`; `effort:` only `low|medium|high|xhigh|max`.
- **A11** `tests/gen-cases.py`'s `block-digest-under-cap` postcheck no longer depends on `$CLAUDE_PLUGIN_ROOT` (unset in postchecks) and fails when the digest is empty.

## 6. Post-change review protocol (agents)

Run after A9 is green, all three in one message, all read-only. Each receives the plan's § 5 and the three-dot diff; none receives this session's reasoning.

| Reviewer | Agent | Question |
|---|---|---|
| **R1 — contract conformance** (dogfood) | `missions:mission-reviewer` | Grade the diff against A1-A10. Per-check verdict: satisfied / not satisfied / cannot tell. Defects with `file:line`. |
| **R2 — impact analysis** | `Explore` (very thorough) | For each changed shell function (`mission_agent_class`, new `mission_agent_model`, journal fields) and each changed template string, list every consumer across hooks, scripts, skills, tests and docs. Flag any consumer the diff did not update, and any doc sentence the diff made false. |
| **R3 — adversarial** (devil's-advocate pattern from `code-review-by-team`) | `general-purpose`, `model: opus` | Try to break three invariants: (a) can a blind reviewer reach commit messages through any tool it is now allowed? (b) does any new tool make a static agent non-static, or let two executors overlap? (c) does any new step spend LLM money outside the measured caps (`repowise update`, graphify labelling)? Verify each claim by reading the code; drop what you cannot verify. |

Then: fix survivors, re-run `tests/run.sh`, run `/simplify` on the diff (reuse / simplification / efficiency / altitude), re-run `tests/run.sh`, and record R1-R3 verdicts in the PR description.

## 7. Testing

**Harness facts that shape the cases** (verified in `tests/run.sh`): `CLAUDE_PLUGIN_ROOT` inside
`run_case()` is always the real plugin dir, so `mission_agent_def` resolves the *shipped, post-change*
`agents/*.md` — T1/T2/T5 need no agent fixture. `postcheck=` runs in a forked subshell of `run.sh`
itself, which sees `run.sh`'s shell variable `$plugin` but **not** `$CLAUDE_PLUGIN_ROOT` (that is
injected only into the script-under-test's `env -i`). The existing `mission-state/block-digest-under-cap`
case uses `$CLAUDE_PLUGIN_ROOT` in its postcheck and therefore **passes vacuously** (`wc -c` of an
empty string). Fix it in this change (A11) and never copy the pattern; prefer the script's own
`rc=2` cap enforcement over an external byte count.

**Decision matrix — what is testable where**

| Logic | Harness | Why |
|---|---|---|
| Agent class with MCP tools (A1/A2) | `gen-cases.py` against the real agent files | `mission_agent_class` is pure awk over frontmatter on disk |
| Reviewer allowlist has no leak tools (A2) | `gen-cases.py` postcheck grep on `$plugin/agents/mission-reviewer.md` | static text property; no dispatch needed |
| Journal `model` (A3) | `gen-cases.py`, extending the dispatch / `agent_return` fixtures | deterministic jq construction in two hooks |
| `check.sh` Seat validation (A4) | `gen-cases.py` under `check/` | pure Python line parser, same shape as `Files:` |
| Digest line (A5) | `gen-cases.py` under `mission-state/`, asserting via the script's own `rc=2` | the script self-enforces 2048 bytes |
| `graphify update` ingest step (D5), seat → `model:` on the Agent call (D8.2), researcher actually querying the graph (D2), reviewer's `graphify affected` step (D3), health delta (D4) | **manual dry run only** | prose interpreted by the orchestrator LLM — no bash entry point |

**Cases to add** (`tests/gen-cases.py`; existing coverage noted so nothing is duplicated)

| # | Case | Expect | Note |
|---|---|---|---|
| T1 | `G/researcher-with-mcp-tools-static` | rc=0; no `.lease`/`.writer` created; journal `"class":"static"` | existing `researcher-never-blocked` already re-validates against the real file — this is the named regression lock |
| T2 | `G/reviewer-with-mcp-tools-executor` | rc=2, `stderr~=execution lease held` | existing `reviewer-blocked-by-lease-journals-wait` covers it today; named lock |
| T3 | `G/reviewer-allowlist-no-leaks` | postcheck `! grep -qE '(get_why\|list_prs\|get_pr_impact\|triage_prs\|get_answer\|mcp__\*__\*)' "$plugin/agents/mission-reviewer.md"` | uses `$plugin` |
| T4 | `G/journal-model-from-override`, `J/journal-model-from-override-return` | `dispatch` and `agent_return` carry `"model":"opus"` when `tool_input.model=opus` | extend `agent_payload()` with `model=None` |
| T5 | `G/journal-model-from-frontmatter` | `"model":"sonnet"` for `mission-worker` with no override | exercises `mission_agent_model()` |
| T6 | `K/check-seat-line-present-ok`, `K/check-seat-line-invalid` | rc=0 with `- **Seat:** opus`; rc=1 + `stdout~=[Ss]eat` with `Seat: gpt` | the no-Seat half is the existing `v2-mission-passes` |
| T7 | `S/digest-includes-intelligence-line`, `S/digest-intelligence-line-cap-still-enforced` | `stdout~=Codebase intelligence:`; rc=2 `stderr~=over its cap` when padded past the cap | |
| A11 | fix `S/block-digest-under-cap` | postcheck uses `$plugin`, and fails if the digest is empty | harness bug found while planning |

**Manual dry run** (`claude --plugin-dir ~/development/context-engineering/plugins/missions`, in the host repo, after the MCP servers are added):

- M1 Dispatch one `mission-researcher` with a graph question; confirm it calls `mcp__graphify__query_graph` and returns `file:line` citations; confirm two run in parallel with no lock file.
- M2 Dispatch a `mission-reviewer` against a fixture patch; confirm its tool list has no `get_why`; confirm it runs `graphify affected` and reports callers. Dispatch a fixture feature carrying `- **Seat:** opus` and inspect the transcript's `tool_input.model`.
- M3 With the graphify MCP server *not* configured, M1 degrades to today's behaviour with no error.
- M4 After `repowise init --index-only` in the host repo: `repowise health --format json` runs and the scrutiny delta step produces output.
- M5 **Settle the caller-identity question.** Run a mission with `MISSION_HOOK_DEBUG=1`, dispatch a `mission-reviewer` that runs one Bash command, and read `.hook-debug.log`: does the Bash `PreToolUse` payload carry `agent_type`? The doc says yes; `mission-shell-guard.sh` keys on it with the execution lease as fallback. If the field is absent in practice, the lease fallback is the mechanism and the comment should say so.

Not covered, by design: reviewer catch-rate (mutation suite — follow-up 1); repowise LLM tools (`get_answer`; not enabled).

## 8. Amendments after the § 6 review (2026-08-31)

R1 (`mission-reviewer`), R2 (`Explore`) and R3 (adversarial, Opus) ran against the first diff. What
changed as a result — every item verified by a new `gen-cases.py` case unless noted:

| From | Finding | Resolution |
|---|---|---|
| R2 2A, R1 High | `agent_stopped` (SubagentStop, no `tool_input`) fell back to the definition's model, so background dispatches booked agent-hours to the wrong seat | `mission-journal.sh` joins `model` from the `dispatch` record; unjoined → unrecorded, never invented (`subagentstop-joins-model-from-dispatch`) |
| R1 D1, R2 3A | `check.sh` rejected the template's own `- **Seat:** opus — rationale` line | value parsed from the head of the line like `Depends on:`; `—`/`none` = omitted; full `claude-…` ids accepted (`check-seat-line-with-rationale-ok`, `check-seat-full-model-id-ok`) |
| R1 D3 | `inherit` is frontmatter-only; on an Agent call it is rejected after the lock is taken | dropped from `SEATS` and the template |
| R1 D4 (pre-existing) | the loop's reviewer template said "do not run git log, git show or git diff yourself" — which `mission-blind-review.sh` blocks | reworded; the verbatim template is now a blind-review case (`run-skill-reviewer-template-passes`, `…-is-current`) |
| R1 D5 | `${#d}` counts characters, the cap is bytes | postcheck uses `wc -c` |
| R3 #1 (Must) | `mcp__repowise__*` on the researcher enabled `get_answer` — an LLM call on repowise's own key, invisible to `mission-spend.sh`; `mcp__graphify__*` enabled the `gh`-backed PR tools | both wildcards replaced by named tools; D2 amended accordingly |
| R3 #2 (Must) | the reviewer holds Bash, so "the leak tools are absent" was prompt-level only; `graphify prs` is an undocumented CLI subcommand that prints PR authors and titles | **new hook `mission-shell-guard.sh`** (PreToolUse · Bash, keyed on the harness's `agent_type`): reviewer's shell refused `git log/show/diff/blame/reflog`, `gh`, `graphify prs`, handoff paths; every caller refused `repowise update`, `repowise init` without `--index-only`, `graphify label/extract`, `cluster-only` without `--no-label`. 19 cases + 6 inertness cases. Reviewer prose and docs reworded to match what is enforced. The plan's "no hook edits for blindness" non-change reasoned about allowlists, not Bash — withdrawn |
| R3 #3 (Should) | static agents exited the serial guard before every cap, so an unbounded researcher fan-out was exempt from the dollar cap | dollar cap moved above the static early-exit (`dollar-cap-blocks-static-too`); count caps still exclude static |
| R3 #4 | a key placed between `tools:` and its items reads as *no tools* → default-deny to writer, silently | **new `scripts/lint-agents.sh`** parses `agents/*.md` the way the hooks do and validates `model:`/`effort:`; gives A10 a real test (`lint-agents/*`) |
| R3 #5 | the planner's probe block ended on a `test -d` that returns 1 when absent | trailing `true` |
| R2 3 | `- **Seat:**` rode into the crosscheck's sealed spec — our judgment of which features are hard | crosscheck seal strips `Seat:` and `Reviewer seat:` lines |
| R2 4, 6 | `snapshot.sh` comment omitted `baseline/`; template journal samples lacked `model` on `agent_launched`/`agent_stopped` | both updated |
| R1, R2 | `get_risk` was recorded as partly LLM-backed | re-verified: no provider call; F4 corrected, `get_risk` stays in `mission-pr-review` |

**`/simplify` pass** (reuse / simplification / efficiency / altitude, four Sonnet reviewers). Applied:
one `seat_problem()` helper in `check.sh` for both seat lines; a `block_llm_spend` row table in the
shell guard; `mission_resolve_model()` in `mission-lib.sh` so both hooks record the same answer; one
`jq` pass for the SubagentStop join (was three over the same record); `lint-agents.sh` rewritten to
call the hooks' own `mission_agent_tools` / `mission_agent_class` instead of a second Python parser
that could drift from them. Skipped, deliberately: scoping the dollar cap back to writers (R3's
correctness point outranks N parallel `mission-spend.sh` runs — one grep and one jq each); the
double `mission_agent_def` read per dispatch (a stat and an awk over a 100-line file); a shared
"is blind" predicate (the two hooks police different agent sets on purpose); merging the seat and
frontmatter-model vocabularies (`inherit` is legal in one and not the other); folding the four
journal-model test cases into a loop (the file's grain is flat explicit cases).

Left as documented limits: the seat → `model:` step and the `graphify update` ingest step are prose the orchestrator follows (manual dry run M1–M3); `docs/missions-agent-workflow-plan.html` is the original design record and still shows the v1 seats table — historical, unmarked.

Counts after review: five agents, **ten** hooks, **eight** scripts; suite 207 cases.

## 9. Follow-ups (not in this change)

1. **Mutation suite** for `mission-reviewer` — plant three defects in a fixture patch, measure catch rate per model (opus/xhigh vs fable/high). This is the gate for any reviewer-model default change (F9).
2. Fix the user-level repowise MCP entry (`<host-repo-v6>` → per-project) and run `repowise init --index-only` in the host repo — user-approved environment change.
3. `graphify` vocab expansion (`references/query.md` Step 0) for the researcher's prompt if M1 shows noisy hits on the 66 MB graph.
