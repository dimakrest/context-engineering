# Mission PRD template

Copy the skeleton, keep the section order, delete the guidance comments. Target under ~200 lines.
Everything in angle brackets is a placeholder; everything else is wording that has survived a
planner reading it.

```markdown
# PRD: <feature, as a user would name it>

Status: ready for `/missions:mission-plan`. Product interview completed <date>.
Architecture, implementation and release measurements are separate work.

Primary tickets: <#id — title>, <#id — title>.

Governing documents: this PRD<, and the constraints in <later review doc> where they add invariants>.
<Any earlier proposals in this folder> are superseded and are not a source of requirements.

## Problem and outcome

<Two or three paragraphs. What goes wrong today, what the user gets when this ships, and the
availability / degradation policy in plain words: what the user receives when the new dependency
is down, slow, or wrong. If a policy choice was made against an earlier draft, say so in bold —
"the user explicitly approved X, replacing Y" — so no reviewer reopens it.>

<One illustrative example, labelled illustrative, walking one input through success and through
the degraded path.>

## Users and scope

| Audience / experience | Required outcome |
|---|---|
| <who> | <what they see> |
| <who inspects or operates it> | <what evidence they can reach> |

<Phase rule: whether a subset is a legitimate first mission, what a subset must name (its
exclusions), and that a subset does not complete the feature. If no first phase was selected,
say so and list it under decisions deferred to `/missions:mission-plan` below.>

Non-goals: <the adjacent channel>, <the legacy path>, <analytics or dashboards>, <the refactor
that would make this easier>, <the irreversible side effect nobody asked to change>. Shared code
changes are allowed only where a contract assertion cannot be met without them, named per
feature with their purpose and regression evidence.

## Product requirements

| ID | Requirement |
|---|---|
| R1 — <name> | <one testable sentence; observable by a user or an authorised inspector> |
| R2 — <name> | <its fail-safe counterpart> |
| … | |

## Response decision

<One sentence on what a row means, e.g. "one selected reply per response, not a new claim of
exactly-once delivery".>

| <Input state> | <Intermediate step> | <User receives> | <Audit trail records> |
|---|---|---|---|
| <normal> | none | <normal output> | <passed> |
| <dependency unavailable> | none | <degraded choice> | <released without valid judgment> |
| <rejected> | <one attempt succeeds> | <replacement only> | <one attempt, selected, not re-checked> |
| <rejected> | <attempt fails technically> | <degraded choice> | <rejected; attempt failed; recovery policy applied> |

<Lifecycle rules around the table: what a reset, closure, handoff or superseding input does to
pending work; what a failure after delivery has started must not do (switch answers, restart,
double-send); what completed side effects must never be re-run for wording.>

## <Existing behaviour> alignment: verified interpretation

<Only if the PRD says "match X". What X actually does, by file (no line numbers), and what
"match" therefore does and does not mean. This section is the answer to "why not escalate /
why not retry / why not withhold" and stops a blind reviewer re-litigating it.>

## Acceptance and testing strategy

| ID / scenario | Observable acceptance | Validation |
|---|---|---|
| A1 — <happy path> | <each observable, comma-separated; include next-turn / downstream effects> | <repo test layer, e.g. mocked unit + Docker integration> |
| A2 — <primary success path> | <one attempt · no second check · nothing leaks before · downstream uses result> | <layers; name a real channel if one is needed> |
| A3 — <dependency unavailable> | <degraded choice · distinguishable from success in evidence · no escalation solely for this> | |
| A4 — <attempt fails> | <degraded choice · evidence keeps the failure · nothing partial leaks> | |
| A5 — <configuration> | <save / reload · inherited settings · already-enabled users need no second switch> | |
| A6 — <display vs inspection> | <what the user surface shows live and after reload · what the inspector sees, correlated> | <persistence integration + UI check> |
| A7 — <ordering, lifecycle, retries> | <order kept · no publish into a closed context · retry does not repeat side effects> | <controlled-timing integration> |
| A8 — <tenancy / isolation, if any scoped data is touched> | <no cross-boundary leakage> | |

<Observation rules: inspect the whole user-visible turn, not the final stored row; cover each
materially different delivery boundary once rather than every case on every transport; shared
code changes carry the relevant existing regression suites.>

<Which of the repo's test layers apply and how they run (e.g. `tests/unit/` mocked, `tests/integration/`
via Docker Compose). Existing candidate files may be listed as starting points. Do not name test files
that do not exist yet.>

## Release gates outside the implementation mission

This section is release evidence, not implementation scope. Measures come from evidence the
feature already retains (<R-id>) and from human review. No new instrumentation, dashboard or
analytics feature is in scope.

| Measure | Purpose |
|---|---|
| <exposure on the success path> | <target zero> |
| <degraded-path rate over eligible cases> | <quantify separately from success> |
| <latency by outcome> | |
| <duplicates, repeated side effects, incremental cost> | |

<Human evaluation paragraph, if quality is part of release: what is compared, on what sample,
what counts as a false rejection.>

<Which numeric thresholds were not supplied, and who sets them before rollout.>

## Engineering questions for the design step

The design step must resolve the following. They do not reopen the resolved product choices.

1. <Where the new behaviour hooks into the existing flow, and how every consumer waits for it.>
2. <Eligibility mapping: which existing exclusions apply, per runtime.>
3. <Deadlines and cancellation: what bounds the wait, and how cancellation differs from failure.>
4. <Persistence and readers: the minimum representation, and every reader that must agree.>
5. <Shared-code changes actually needed, and their regression evidence.>

Decisions deferred to `/missions:mission-plan`, because they shape the mission rather than the
product: <delivery families in the first mission>; <whether a feasibility proof is the first
milestone>; <the deadline rule that still needs a number>; <blast radius and reviewer seats>;
<caps and autonomy ceiling>.

## Current implementation evidence

Source inspected on <date> at `<commit>`; findings describe that checkout, not a deployment.
Line anchors are deliberately absent; the design step establishes current ones.

| Finding | Source |
|---|---|
| <what exists today, in one sentence> | <file paths, no line numbers> |

## Sources and decision provenance

| Source | Use |
|---|---|
| <ticket> | <what was taken from it; read date> |
| <earlier proposal> | Superseded proposal. <one clause on what it got wrong>. Engineering reference only. |
| <inaccessible doc> | Not retrievable; not used as evidence. |

Interview decisions, all on <date>:
- <decision, in the user's words where a choice was reversed>
```

## What the planner will do with each section

| Section | Becomes |
|---|---|
| Goal, non-goals, blast radius | `mission.md` |
| R-table and decision matrix | The bulk of `contract.md`, one `A0nn` per observable |
| Acceptance table's *Validation* column | Proof class tags (`structural` / `conversational` / `interface`) |
| Engineering questions | `mission-design`'s research fan-out |
| Current implementation evidence | Seeds for `state.md` "key facts", after re-anchoring |
| Decisions deferred | The interview's agenda |
| Release gates section | Nothing — that is the point |
