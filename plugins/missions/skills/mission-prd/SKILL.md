---
name: mission-prd
description: Write, or audit and repair, a PRD so it can be handed straight to /missions:mission-plan and /missions:mission-design without confusing them. A mission PRD is read by agents, not people - every "must", line number and metric in it becomes a task, a citation or an assertion attempt. Use this whenever the user wants a PRD, product requirements, a spec or acceptance criteria for work that will run as a mission, asks "is this PRD ready for the mission / the plan phase / the design phase", or points /missions:mission-plan at a requirements document that was not written with missions in mind. Writes zero product code and zero mission files.
user_invocable: true
---

# /missions:mission-prd — a PRD an agent can plan from

`/missions:mission-plan` turns a PRD into a validation contract; `/missions:mission-design` turns the
repo into guidelines the workers are bound to. Both are run by agents that read the PRD literally
and have no memory of the meetings behind it. A PRD written for a human product review is full of
things that are harmless to a person and poisonous to a planner: an open question phrased as
"must be reconciled", a release metric that looks like a requirement, a line number from a
checkout three weeks old, a link to a superseded proposal whose contract says the opposite.

**The test of a mission PRD:** could the planner lift the assertions out of it without inventing
anything, and would the design step find its open questions without inheriting an architecture?

**You write no product code and no mission files here.** The output is one document in the
repo's plan folder (`docs/plans/<slug>-prd-plan.md` or wherever this repo keeps plans). The
mission files come later, from `/missions:mission-plan`.

## Two modes

- **Author** — no PRD exists. Interview, then write from `references/prd-template.md`.
- **Audit** — a PRD exists. Read it against the two tables below, report each risky passage
  with its line number and a concrete rewrite, and apply the rewrites when the user says so.
  Do not rewrite silently: the person who wrote the PRD needs to see what moved and why.

In both modes, finish with the handover in the last section.

## Step 0 — know what the consumers lift

Read the PRD the way each downstream step will. The planner and the design step take different
things; a PRD is good when each finds its material where it looks and nothing else.

| Consumer | Lifts from the PRD | Must not find |
|---|---|---|
| `/missions:mission-plan` (contract) | Goal in one sentence · non-goals · what "done" looks like to a user · a decision matrix · acceptance scenarios with their fail-safe pairs · which repo test layer proves each · blast radius · phase/scope boundaries · the decisions deliberately deferred to the interview | Assertions that name code that does not exist yet · metrics no validator can prove · a selected architecture · open questions phrased as requirements |
| `/missions:mission-design` (guidelines) | Open engineering questions, numbered · current-state findings as file references · constraints from shared code the feature touches · verified interpretation of any "match existing behaviour X" | Line numbers from a stale checkout · a preferred module layout · "may propose refactoring" invitations |
| `mission-researcher` / `mission-worker` (grep the plan folder) | One governing document, named as such | Earlier proposals that still read as live, or as work to do |

If the repo has a docs-first rule (a wiki index, `CLAUDE.md`), it applies here: read the pages for
the affected area before writing a current-state section, and cite them rather than re-deriving.

## Step 1 — interview (author mode) or extract (audit mode)

Resolve these before writing. In audit mode, check whether the PRD already answers them and list
the ones it does not as "decisions deferred to `/missions:mission-plan`" rather than guessing.

- **Goal in one sentence.** If it needs two, it is two PRDs.
- **Non-goals, explicitly.** These are what stop a worker "improving" a neighbouring subsystem.
  Write the tempting ones: the adjacent channel, the legacy path, the analytics nobody asked for,
  the refactor that would make the feature easier.
- **What the user sees when it is done** — per audience, in one table. Not what the developer sees.
- **The decision matrix** — for every input state the feature distinguishes, what the user
  receives and what the audit trail records. This one table generates most of the contract.
- **Failure and degradation policy**, stated as behaviour: what happens when the dependency is
  down, slow, malformed, or returns something usable-but-wrong. "Match voice" or "match the
  existing X" is not a policy until someone has read X and written down what it actually does.
- **Blast radius** — migrations, authorization or tenancy boundaries, money (including anything
  that changes a count that feeds billing), shared code other features depend on, irreversible
  side effects outside the repo.
- **Phase boundary** — is a subset a legitimate first mission? If so, say what a subset must
  name (its exclusions) and that it does not complete the feature.
- **What is deliberately left to the mission interview:** delivery families in the first mission,
  deadline rules that need a number, caps and autonomy, reviewer seats. Name them; do not answer
  them with placeholders.

Push back. A PRD that "targets everything, phased if needed" with no phase criterion hands the
planner a scoping decision it cannot make.

## Step 2 — write the acceptance section so the contract falls out of it

The planner will split each scenario into `A0nn` assertions, tag a proof class and give each a
proof budget. Write scenarios so that split is mechanical:

- **Behavioural, not structural.** Name no function, class, column, module or file that does not
  already exist. The test: *could two engineers satisfy this with completely different code, and
  would both be right?* If the scenario only makes sense for one design, it is a design note, not
  an acceptance criterion.
- **One row per input state, listing its observables.** The row for "correction succeeds" says: one
  attempt, no second judgment, the draft never reaches the customer, partial text never reaches
  the customer, the next turn uses the correction. Five observables in one row is fine; the
  planner splits rows, not sentences.
- **Every "it works" has its "it refuses / fails safe" row.** Adversarial validators need
  something to bite on. Unavailable dependency, malformed-but-recoverable output, timeout, retry,
  stale work landing after the conversation moved on.
- **Say where the observation is made.** "Inspect the whole customer-visible turn, not the final
  stored message" is the difference between an assertion and a wish. If the observation needs a
  real channel (a call, a chat session, a screen), say so: that is what makes an assertion
  `conversational` or `interface` and it costs real money to prove.
- **Name the repo's test layers, not test names.** "Mocked unit for policy branches; Docker
  integration for the real conversation and persistence boundary" tells the planner which
  validator runs. Candidate existing files may be listed as starting points; new test files may
  not, because they do not exist yet.
- **Isolation is never implied.** If the feature touches data scoped to a tenant, account or
  user, write the leakage row.

## Step 3 — sweep for planner poison

These are the passages that survive human review and derail an agent. Audit mode reports them by
line; author mode simply never writes them.

| Pattern | Why it derails the plan phase | Rewrite |
|---|---|---|
| **Open-work phrasing in provenance.** "Proposal X's contract at lines 15–30 must be reconciled with this PRD." | Reads as a task. A researcher follows the pointer, lands on a contract that contradicts the interview, and brings it back as a requirement. | Past tense, one line: "Superseded. Assessed in <doc>; not a source of requirements." Drop the line pointers. |
| **Release metrics without a boundary.** A "measure the following" table and a "human evaluation should compare…" paragraph under a heading like *Success*. | No scrutiny or behaviour validator can prove a quality rate or a latency percentile. The planner either writes unprovable assertions or invents instrumentation features the non-goals forbade. | Move under **Release gates outside the implementation mission**. State the source of each measure (existing logs, human review) and "no new instrumentation or analytics feature is in scope". |
| **Stale line anchors.** `bot/foo.py:806` from a checkout that has moved. | Copied into `state.md` key facts, then handed to every worker as truth. The design step re-anchors everything anyway. | Keep the file name, strip the line, add one sentence saying anchors come from the design step. |
| **An open invitation inside the non-goals.** "Shared refactoring may be proposed with its purpose and cost explicit." | The one sentence a 2am worker will quote back. | "Shared changes only where a contract assertion cannot be met without them, named per feature with regression evidence." |
| **Authoring meta-notes.** "No application tests were executed for this documentation task." "The testing-strategy review removed…" | Describe how the document was made, not what to build. Noise at best; at worst read as a status of the feature. | Delete. |
| **An architecture pick dressed as a requirement.** "A coordinator owns each response." "Add a `status` column." | The contract becomes a description of a design nobody has validated, which is the failure the whole workflow exists to prevent. | Move to the design-questions section as a question: "Where is the response owned, and how does every consumer wait for selection?" |
| **"Bounded" with no bound.** "Judging must complete within a bounded time." | An assertion needs a number or a rule. Without one the planner picks a constant from an old plan. | Give a rule if no number exists: "bounded by the existing turn deadline minus a publication reserve; on exhaustion take the matching recovery branch". Note the number as a deferred decision. |
| **"Match existing behaviour X" without a verified interpretation.** | X usually does something subtler than the ticket says. The planner writes an assertion for the ticket's version. | Keep a short *verified interpretation* section: what X actually does, by file, and what "match" therefore does and does not mean. |
| **Two governing documents that do not say which wins.** A PRD plus a later impact review, each with constraints. | Whichever an agent reads first governs. | One *Governing documents* line at the top naming both and the precedence; fold any new invariants from the later doc into the acceptance table. |

## Step 4 — structure

Use `references/prd-template.md`. Section order matters because agents skim from the top:

1. Status, tickets, **governing documents** line
2. Problem and outcome — including the availability/degradation policy in plain words
3. Users and scope — audience table, phase rule, non-goals
4. Product requirements — `R1..Rn`, each one testable sentence
5. Response/decision matrix — the table plus the lifecycle rules around it
6. Verified interpretation of any behaviour being matched
7. Acceptance and testing strategy — the scenario table, layers, observation rules
8. Release gates outside the implementation mission — measures, human evaluation, unset thresholds
9. Engineering questions for the design step — numbered; ends with **decisions deferred to
   `/missions:mission-plan`**
10. Current implementation evidence — findings by file, no line anchors, dated to a commit
11. Sources and decision provenance — every earlier proposal marked superseded

Keep it under ~200 lines. A PRD the planner has to summarise before using is already too long.

## Step 5 — handover

Report tightly:

- **Author mode:** the path; the decisions deferred to the mission interview (the planner will ask
  them; the user should arrive with answers); the two or three scenario rows you are least sure
  are behavioural rather than structural.
- **Audit mode:** the table of passages by risk with line numbers and rewrites; whether you
  applied them; anything the PRD still leaves to the interview. The high-risk rows are the ones
  worth the user's attention: superseded proposals still phrased as work, and metrics that will
  become unprovable assertions.

Then point at the next step: `/missions:mission-plan` with the PRD and its governing companions
named as inputs, and any superseded documents in the same folder named as such.
