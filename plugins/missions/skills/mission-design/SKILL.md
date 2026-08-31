---
name: mission-design
description: The architecture step of a mission, run after /missions:mission-plan and before /missions:mission-run. Fans out read-only mission-researcher agents to find the repo's existing patterns, then writes design.md - numbered architectural guidelines (D001..) anchored to file:line exemplars - which binds every mission-worker and travels to the blind reviewers. Mandatory - /missions:mission-run refuses to dispatch without it. Writes zero product code. Use after the contract is reviewed, or when the user says "/missions:mission-design", "design the mission", or asks for architectural guidelines before implementation.
user_invocable: true
---

# /missions:mission-design — architecture before implementation

The contract fixes **what** done means. This step fixes **how** the code should be shaped — before
any worker exists to have opinions. Workers run in clean, isolated contexts; without a shared design
each one rediscovers the repo's conventions alone, and when two patterns coexist in the codebase
(they always do), different workers imitate different ones. `design.md` is how N workers build one
codebase instead of N. It is mandatory: `/missions:mission-run` refuses to dispatch a worker without it.

Phase stays `planning` — the contract-first hook still blocks product code, deliberately. You write
exactly one run artifact: `.missions/<slug>/design.md`.

## Preconditions

`contract.md` and `features.md` exist and passed the planner's coverage gate; otherwise stop and
point to `/missions:mission-plan`. Read `mission.md`, `contract.md`, `features.md` and `state.md` first —
the design constrains the features that exist, not the ones you would have chosen.

## Step 1 — explore: find what the repo already does

Fan out `mission-researcher` agents (Agent tool, `subagent_type: mission-researcher`) — read-only,
parallel-safe, several at once, one bounded question each. Ask for the **canonical example, not a
survey**: the `file:line` a worker should open and imitate. Do not read the whole codebase yourself.

Cover, at minimum, whatever of these the mission touches:

- The closest existing analogue to each feature — the thing to imitate, and how it is shaped
- Layering and module boundaries on the affected paths — where each kind of code lives
- Error handling, validation and logging conventions on those paths
- How this repo tests this kind of code — layer, fixtures, naming, where the tests live
- Data access — how queries, transactions and tenancy/ownership scoping are done on the affected models
- Naming and file-placement conventions in the affected directories
- Anti-patterns — what the repo's docs forbid, and any legacy pattern mid-migration that must not
  be imitated even though grep finds it everywhere

Weigh the answers before adopting them: a researcher citing one file has found *a* pattern, not
*the* pattern. Where two patterns coexist, pick one, say why, and record the loser under
anti-patterns so no worker picks it independently.

## Step 2 — write design.md

Schema: `${CLAUDE_PLUGIN_ROOT}/templates/MISSIONS_TEMPLATES.md`. Guidelines carry ids `D001..`, are never
renumbered, and each must be **checkable by reading a diff** and anchored to a `file:line` exemplar.
Per-feature sections list which guidelines and exemplars apply — the loop copies from there,
verbatim, into every worker and reviewer dispatch.

A guideline is a constraint, not a tutorial: "new service functions follow the
Router→Service→Repository split as in `orders/service.py:40`; no DB access from a router" — not an
essay on layered architecture. And design decides *shape*, never *scope*:

- A guideline never weakens, replaces, or reinterprets an assertion. The contract outranks the
  design everywhere they touch.
- If designing reveals a missing feature, a wrong assertion, or a decomposition that can't carry the
  architecture, **stop and tell the user** — that is a contract change, and the user owns the
  contract. Never patch `contract.md` or `features.md` from here. Once the user has decided,
  `/missions:mission-amend` is what applies it: it maps the blast radius across all five files, gates on a
  coherence check, and re-runs the crosscheck when the contract moved.

## Step 3 — hand over

Append the design summary to the human-facing plan doc (`docs/plans/<slug>-plan.md`), journal a
`decision`, and report tightly:

- each guideline, one line, with the pattern it is anchored to
- the alternatives you rejected and why (one line each)
- the two or three guidelines you are **least sure about** — the ones a worker is most likely to
  fight. The user corrects `design.md` now; after this, every worker obeys it and every reviewer
  grades against it.

If the user wants the architecture checked by something outside this vendor before committing to
it, `/missions:mission-crosscheck design` derives it independently from a sealed spec and reports the
divergences. Optional; the convergences are as useful as the disagreements.

Then stop. `/missions:mission-run` is a separate, explicit invocation by the user.
