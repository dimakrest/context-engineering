# Mission file templates

The on-disk schema for `.missions/<slug>/`. Read by `/missions:mission-plan` (writes them), `/missions:mission-design`
(writes `design.md`), `/missions:mission-run` (reads and updates them), `mission-worker` (writes handoffs),
the validators (write verdicts), and `/missions:mission-pr-review` (writes `validation/pr-review*`).

`.missions/` is git-ignored. Product code lands on a branch through normal commits; run state stays
local. The contract is the exception — `/missions:mission-plan` copies it into `docs/plans/<slug>-plan.md` so a
human reviewer can see what "done" was defined as without reading the run state.

```
.missions/<slug>/
  mission.md          goal, non-goals, constraints, model seats, budget (dollar / dispatch / wall-clock caps)
  contract.md         THE artifact — assertions with proof budgets, written before any code
  design.md           architecture — guidelines D001.. + pattern inventory, written before any code
  features.md         F001.. → milestone, assertions, files, range, procedures, status
  state.md            the HOT file — fenced machine block + open issues + standing constraints; capped
  archive/M1.md       the COLD file — closed milestones, moved by scripts/mission-archive.sh
  .writer  .lease     hook-owned locks: active writer, host execution lease (never edit by hand)
  patches/F001.patch  the exact per-feature diff a blind reviewer receives (scripts/mission-patch.sh)
  handoffs/F001.md    one per feature, schema-enforced
  validation/         M1-scrutiny.md · M1-behavior.md · M1-review-F001.md · pr-review.md
  crosscheck/          progress.md · pass1-report.md · report.html  (external review; raw
                       transcript and sealed package live OUTSIDE the mission dir)
  followups.md        the finding registry — clustered, dispositioned; repairs become features
  journal.jsonl       append-only event log (hooks write dispatch / agent_return / locks / spend / model)
  baseline/health.json  `repowise health` at plan time, when the repo is indexed — the scrutiny
                       validator reports the delta against it
```

---

## mission.md

```markdown
# Mission: <slug>

**Goal (one sentence):** <what this mission delivers>

## Non-goals
- <the neighbouring thing a worker must not "improve" at 2am>

## Constraints
- Branch: `mission/<slug>` (never pushed, never merged by the loop)
- Blast radius: <migrations / authorization boundaries / money / third-party side effects — or none>
- Autonomy ceiling: advisory (default — the loop proceeds under stated assumptions and surfaces them) | halt at every milestone
  (never "halt on validation failure": first-pass failure is the normal case, and wiring it as a halt pages the human at every milestone by design)

## Model seats
Defaults live in the agent definitions and need no line here: worker Sonnet 5 · reviewer Opus 5 at
`xhigh` · researcher and scrutiny Sonnet 5 · behavior Opus 5 · orchestrator = the session's model.
Record deviations only — `check.sh` validates them, the loop passes them as `model:` on the Agent
call, and the journal records what actually ran (`journal-metrics.sh` sums it per model).
- Reviewer seat: fable   # optional — when the blast radius includes auth, money or tenancy
- Per-feature seats go in features.md (`- **Seat:** opus`), not here

## Budget
- Dollar cap: $<n> — measured from the harness (scripts/mission-spend.sh), enforced by the serial guard
- Dispatch cap: <n> — worker + reviewer + validator dispatches, counted from the journal
- Active wall-clock cap: <n> h — sum of journaled agent duration_s
- Repair rounds per assertion: 2 — a third repair for the same assertion is a diagnosis problem
- Terminal-review reserve: 15% — of the dollar cap, kept for /missions:mission-pr-review
- Behavior-validation cap: <n> live runs per milestone (calls, sessions — whatever costs money here)
- Token estimate: <number> — informational only; never a governor (the last mission's cap was denominated in a unit nobody measured)
```

A cap is a decision made while thinking clearly. Raising one mid-mission is journaled as `cap_raised`
with the reason, and the convergence gate counts it against the mission.

---

## contract.md

Assertions are **observable behavior**, written before any code, never renumbered. Status is set by
validators only — a worker may move an assertion to `claimed`, never to `proven`.

```markdown
# Validation contract — <slug>

Status values: `unproven` → `claimed` (worker says so) → `proven` (a blind agent showed it)

| ID | Assertion | Proof class | Feature(s) | Status | Evidence | Proof budget |
|---|---|---|---|---|---|---|
| A001 | After a tool call succeeds, that tool's failure streak is zero | structural | F001 | proven | tests/unit/test_streak.py::test_reset_on_success | min: named test; max: 1 pinning feature |
| A002 | A tool failing for account A does not affect account B's streak for the same tool | structural | F001 | unproven | — | min: mutation (tenancy); max: 1 pinning feature |
| A003 | When the user interrupts mid-sentence the system stops within one turn and answers the new question | conversational | F004 | unproven | — | min: 1 live run; max: 2 live runs |
| A004 | An account with no alert config sees no alert rows on the dashboard, not an error | interface | F006 | unproven | — | min: playwright; max: 1 run |
```

Proof classes: `structural` (the repo's automated tests) · `conversational` (exercising the system
through a real conversational channel) · `interface` (Playwright over the real UI). The tag decides
which validator runs and what a milestone costs in wall-clock time and live spend.

**Proof budget** is both a floor and a ceiling. *min* names the weakest evidence that closes the
assertion — a named load-bearing test; `mutation` or `pre-fix failure` is required for tenancy,
authorization, arithmetic, concurrency and error paths (an inert test that cannot fail is not
evidence). *max* bounds how much pinning the assertion may consume — default one pinning feature;
hardening beyond that is accepted debt in `followups.md`, never a new feature. The last mission spent
26 consecutive features on test-only pinning of already-correct behaviour while its ten interface
assertions went unproven; the budget is what stops that. The column stays last so `check.sh` and
older parsers keep finding Feature(s).

---

## design.md

Written by `/missions:mission-design` after the contract, before any code — **mandatory**; `/missions:mission-run`
refuses to dispatch a worker without it. Guidelines carry ids `D001..`, are never renumbered, and
each is checkable by reading a diff and anchored to a `file:line` exemplar found by the exploration
agents. The loop copies a feature's guidelines verbatim into its worker and reviewer dispatches.
The contract outranks the design everywhere they touch — a guideline never reinterprets an assertion.

```markdown
# Design — <slug>

## Guidelines
| ID | Guideline | Exemplar | Applies to |
|---|---|---|---|
| D001 | New service functions follow the Router→Service→Repository split; no DB access from a router | `orders/service.py:40` | all |
| D002 | Streak state lives on the existing `ToolHealth` model — no new table, no parallel store | `models/tool_health.py:12` | F001, F002 |

## Pattern inventory
- <pattern> — canonical example `file:line` — <one line on its shape and when it applies>

## Anti-patterns for this mission
- <the coexisting legacy pattern that must not be imitated, where it lurks, and why it lost>

## Per-feature
### F001
- **Guidelines:** D001, D002
- **Imitate:** `file:line` — <the closest existing analogue to this feature>
```

---

## features.md

One feature = one coherent, independently testable slice = one worker in one clean context window.
Target 2–5 features per milestone; findings with the same root cause are repaired together;
test-only and prose-only fixes do not get a feature unless they move a release gate. `check.sh`
rejects a feature list whose count exceeds the distinct files it touches.

```markdown
# Features — <slug>

## M1 — <milestone name>

### F001 — <title>
- **Assertions:** A001, A002
- **Files:** `alerting/service.py`, `alerting/repository.py`, `tests/unit/alerting/test_streak.py`
- **Seat:** opus — optional; only when the feature warrants a model above the worker's default (sonnet, opus, haiku, fable, or a full claude-… id; omit the line for the default)
- **Procedures:** tests at the repo's mocked layer under `alerting/`; no migration; update the docs page for this area
- **Depends on:** —
- **Out of scope:** the dashboard query (F005)
- **Status:** done · commit `84182bf`
- **Range:** `9324706`..`84182bf` — the exact base..head the blind reviewer's patch was cut from
```

Status: `pending` → `active` → `done` | `blocked`. **Files** is written at planning time (the
feature/file gate reads it); **Range** when the handoff lands (the first feature's base is the
merge-base with `origin/main`, every later one's is the previous feature's head).

---

## state.md — the hot file

One file, one source of truth: no agent-to-agent messaging. But nobody reads it whole: agents are
briefed with the **digest** (`scripts/mission-state.sh`, ≤ 2 KB — the fenced block, the locks, open
issues, standing constraints, `resume_next`) plus id-scoped reads on demand. The file is **capped**
(`state_cap_lines`, default 200; the serial guard refuses to dispatch a writer over it) and closed
milestones move to `archive/M<n>.md` (`scripts/mission-archive.sh`). The last mission's state file
reached 1,182 lines, 35% closed history, broadcast into ~100 briefings, and after a compaction the
loop re-read only its first 80 lines — never reaching the rulebook.

The fenced block is the only part a machine parses. Keep it bare: one `key: value` per line, no prose.
The orchestrator owns this file; the hooks never write it (locks live in `.writer` / `.lease`).

```markdown
# Mission <slug> — state

```mission-state
phase: planning            # planning | implementing | validating | negotiating | pr | halted | done
milestone: M1
spend_usd: unknown         # from scripts/mission-spend.sh at every state update; "unknown" is honest
resume_next: dispatch F003 (M1 feature 3 of 4); F002 handoff ingested, no open issues
state_cap_lines: 200
```

**Branch:** mission/<slug>

## Open issues — these block the next feature
- F002 handoff: integration test skipped, the test stack wouldn't start

## Standing constraints for every agent
> **This section is the project's rulebook.** The worker, reviewer and validator agents are
> project-agnostic — this is the only place they learn what *this* repo requires. Write it
> concretely, with real commands and real paths. Vagueness here produces a worker that guesses —
> but keep it under ~1.5 KB: reference the repo's own rules files by path rather than copying them.

- Never push, merge, `--no-verify`, `--admin`
- <how tests run here — the exact invocation, and which layer to pick>
- <database safety — which hosts/ports are read-only, who is allowed to migrate>
- <docs this repo expects updated, and where plans live>
- <the repo's PR-creation and adversarial-review skills, if it has them — /missions:mission-pr-review uses both>
- <anything with a real-world side effect that needs human confirmation>
- Codebase intelligence: graphify=cli+mcp (graphify-out/, <date>) · repowise=index (.repowise/) — or `none`; the planner's probe writes it, every agent branches on it

## Key facts established during planning (do not re-research)
- <seams, file:line citations, decisions already settled — saves every worker the lookup>

## M1 — <current milestone, working notes>
<what is in flight; when M1 closes this section is archived>

**Last updated:** <when, by which step>
```

`phase` values: `planning` → `implementing` → `validating` → `negotiating` → … → `pr` → `done`, or
`halted`. `pr` is the one phase in which pushing is allowed. The hooks normalise a few common
misspellings (`implementation` → `implementing`) and warn on anything outside the list; an unknown
phase keeps the mission *active*, never silently inert.

**Legacy header.** Missions written before v2 carry `**Phase:**` / `**Active writing agent:**` prose
lines instead of the block. Every hook and script still reads those (with a stderr warning); the
size cap and the lock files apply only to block-style missions.

---

## handoffs/F00n.md

Written by the worker. The loop **rejects** a handoff with a missing section, a command without an
exit code, or a commit that isn't in `git log`.

```markdown
# Handoff F003 — <title>

## Status
complete | partial | blocked

## Assertions claimed
- A005 — satisfied by `services/alerting.py:88`; streak resets inside the same transaction
- A006 — NOT satisfied; see issues below

## Completed
<what exists now that didn't before>

## Left undone
<explicitly. "nothing" is a valid answer and must be written, not omitted>

## Commands run
| Command | Exit | Note |
|---|---|---|
| <the repo's mocked-test invocation, scoped to the module> | 0 | 14 passed |
| <the repo's linter> | 1 | 2 fixed, re-run clean |

## Issues discovered
<anything the next agent needs. These block progress until resolved or deferred.
"none" must be written explicitly.>

## Procedures followed
<which of state.md's standing constraints applied and how — test layer chosen, docs updated,
migration routed rather than written.
And design conformance, per guideline you were given:
- D001 — followed (`services/alerting.py:88` mirrors the exemplar)
- D002 — DEVIATED: <what you did instead, and why> — needs an orchestrator decision
A silent deviation is a defect; the blind reviewer grades the same guidelines.>

## Commit
`<sha>` F003: <subject>
```

---

## validation/M<n>-*.md

`M1-scrutiny.md`, `M1-behavior.md`, `M1-review-F00n.md` (one per feature). Each carries a
per-assertion verdict table with evidence, and defects with `file:line` or a call id. Verdicts are
`proven` / `not satisfied` / `cannot tell` / `not reached` — never "probably fine".

The terminal pass adds two files: `pr-review.md` — `/missions:mission-pr-review`'s progress file (PR number,
step checklist, findings table), which is what makes an interrupted review re-entrant — and
`pr-review-report.html`, the final self-contained findings report. The progress-file schema lives in
that skill.

---

## followups.md

The finding registry. Defects are never patched in place by the validator that found them — but a
finding is not automatically a feature either. Every finding is **clustered by root cause** and
**dispositioned**; one cluster is repaired by one feature; one-line repairs sharing a cause are
batched into a single repair round. The convergence gate (`scripts/mission-converge.sh`) counts
entries here against features, and `check.sh` rejects an unclustered or undispositioned entry.

```markdown
# Follow-ups — <slug>

## FU001 — cross-tenant leak in the streak query (from M1-review-F001)
- **Assertion:** A002
- **Found by:** mission-reviewer, `repositories/alerting.py:41` — no tenant filter
- **Severity:** high | medium | low
- **Cluster:** C01 — repository queries missing the tenant predicate
- **Blocking:** yes
- **Disposition:** repair as F007 | accept as known limitation | waived by <who>, <why>

## FU002 — same omission in the summary query (from M1-review-F002)
- **Assertion:** A002
- **Found by:** mission-reviewer, `repositories/alerting.py:77`
- **Severity:** high
- **Cluster:** C01
- **Blocking:** yes
- **Disposition:** repair as F007 — same cluster, same feature
```

The `(from M<n>-…)` tag in the title is what the convergence gate uses to attribute a finding to a
milestone; keep it.

---

## journal.jsonl

Append-only, one JSON object per line. Feeds `/missions:mission-status`, the caps and
`scripts/journal-metrics.sh`. Only `ts` and `event` are guaranteed; parsers tolerate unknown keys.

Hook-written (measured, not remembered):

```jsonl
{"ts":"2026-08-18T14:02:11Z","event":"dispatch","agent":"mission-worker","class":"writer","model":"sonnet","feature":"F003","dispatch_id":"toolu_01…","session_id":"…"}
{"ts":"2026-08-18T14:02:11Z","event":"session_cost","session_id":"…","usd":41.20}
{"ts":"2026-08-18T14:02:12Z","event":"agent_launched","agent":"mission-worker","via":"PostToolUse","model":"sonnet","feature":"F003","dispatch_id":"toolu_01…","agent_id":"a318…","status":"async_launched"}
{"ts":"2026-08-18T14:41:52Z","event":"agent_stopped","agent":"mission-worker","via":"SubagentStop","model":"sonnet","feature":"F003","dispatch_id":"toolu_01…","agent_id":"a318…","duration_s":2381}
{"ts":"2026-08-18T14:41:52Z","event":"agent_return","agent":"mission-worker","via":"PostToolUse","model":"sonnet","feature":"F003","dispatch_id":"toolu_01…","agent_id":"a318…","duration_s":2381,"status":"completed"}
{"ts":"2026-08-18T14:41:52Z","event":"writer_lock_cleared","reason":"returned","via":"PostToolUse","lock":"agent=mission-worker feature=F003 …"}
{"ts":"2026-08-18T14:41:52Z","event":"lease_released","reason":"returned","via":"PostToolUse","lock":"…"}
{"ts":"2026-08-18T15:02:00Z","event":"lease_wait","agent":"mission-reviewer","feature":"F004","holder":"agent=mission-validator-scrutiny …"}
```

Loop-written:

```jsonl
{"ts":"2026-08-18T14:43:00Z","event":"handoff_ingested","feature":"F003","status":"complete","commit":"a1b2c3d","range":"9324706..a1b2c3d"}
{"ts":"2026-08-18T14:44:03Z","event":"verdict","validator":"mission-reviewer","feature":"F003","assertions":{"A005":"satisfied","A006":"not satisfied"}}
{"ts":"2026-08-18T15:10:00Z","event":"halt","class":"advisory","reason":"A006 not satisfied on first pass","assumption":"implementation defect, repair as F007 in cluster C02"}
{"ts":"2026-08-18T15:10:00Z","event":"halt","class":"block","reason":"dispatch cap 60 reached","decision_needed":"re-plan M3 or stop"}
{"ts":"2026-08-18T15:12:00Z","event":"cap_raised","what":"Dollar cap","from":150,"to":200,"by":"user","why":"…"}
```

Events: `dispatch` · `agent_launched` (background dispatch started) · `agent_return` (waited dispatch
finished; `duration_s` from the harness) · `agent_stopped` (the agent's own end; `duration_s` only for
background dispatches, joined by `agent_id`) · `session_cost` · `writer_lock_cleared` · `lease_released` ·
`lease_wait` · `handoff_ingested` · `verdict` · `commit` · `halt` (with `class: advisory|block`) ·
`resume` · `decision` · `cap_raised` · `amendment` · `note`. `duration_s` is `null` when the return
cannot be joined to its dispatch — never estimated.

`amendment` is written by `/missions:mission-amend` and carries `files` and `contract_changed`; a `decision`
that supersedes an earlier one carries `supersedes` with the id it replaces. `note` is for something
learned that changes no artifact — where a stale copy of an artifact was found, why a run was voided.

```jsonl
{"ts":"...","event":"amendment","step":"scope-cut","files":["contract.md","features.md","design.md"],"summary":"F004 deleted, A015 retired; D003-D007 dropped F004 from their applicability columns","contract_changed":true}
{"ts":"...","event":"decision","step":"mission-design","id":"D013","supersedes":"D013","summary":"REVERSED. Unpaginate-refine-slice replaced by a SQL twin plus an executed parity test","exemplar":"db/models/outbound_conversation_queue.py:124-136"}
```

## The `Amendments` table

`contract.md`, `features.md` and `mission.md` each grow one when `/missions:mission-amend` changes them.
Recorded rather than edited silently, so a reviewer can see what moved without diffing.

```markdown
## Amendments after `/missions:mission-plan`

| When | What | Why |
|---|---|---|
| Post-`/missions:mission-design`, pre-`/missions:mission-run` | **A015 retired**; A001 and A016 narrowed to one surface | It asserted "a comparison between two periods", but the endpoint takes one date range and compares escalation cohorts — `analytics_router.py:142-153`, `analytics_service.py:213-215` |
```

Two rules the rest of the machinery depends on:

- **Ids are never renumbered or reused.** A retired id leaves a gap.
- **A retired id is declared here, in `contract.md`'s own table.** `check.sh` reads this to tell a
  retired id from a dangling reference, and without it a gap in the numbering reads as a corrupt
  file — or gets reported as a defect by the next blind reviewer.
