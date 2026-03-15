---
name: plan-and-review-by-team
description: Autonomous 8-agent team for creating or reviewing implementation plans. Full-stack capable with cross-review debate, tiebreaker, and chief-reviewer sign-off. Can also review existing plans.
user_invocable: true
---

# /plan-and-review -- Autonomous Plan Creation & Review with Team

Create or review an implementation plan using an 8-agent team that researches, writes, debates, and signs off autonomously. The user only sees the final reviewed plan.

## Mode Detection

Parse the user's input to determine the mode:

- **Create mode** (default): No existing plan provided. Run all phases (research, write, review, revise, sign-off).
- **Review mode**: User provides a path to an existing plan (e.g., `thoughts/shared/plans/2025-01-15-feature.md`). Skip phases 1-2 (research + writing), start directly at phase 3 (reviews). The plan-writer only participates in revision (phase 5+).

Optionally, the user may also provide a **ticket path** (e.g., `thoughts/shared/tickets/2025-01-15-feature.md`). If provided, pass it to the chief-reviewer for ticket alignment validation.

## Step 1: Create the Team

Use `TeamCreate` to create a team named `plan-and-review`.

## Step 2: Create Tasks for All Phases

Use `TaskCreate` to create tasks for each phase. Use `blockedBy` to enforce ordering.

**In review mode** (existing plan provided): skip tasks 1-3. Create tasks 4+ with no initial blockedBy on tasks 4, 5, 6.

**In create mode** (default): create all tasks as shown below.

```
Task 1: "Research: Map all relevant code, components, services, APIs, DB models, state, routing"
  - status: pending, no blockedBy

Task 2: "Research: Find similar implementations following current standards, extract target patterns"
  - status: pending, no blockedBy

Task 3: "Write initial plan draft based on research findings"
  - blockedBy: [1, 2]

Task 4: "Frontend review: Review frontend aspects of the plan"
  - blockedBy: [3]  (no blockedBy in review mode)

Task 5: "Backend review: Review backend aspects of the plan"
  - blockedBy: [3]  (no blockedBy in review mode)

Task 6: "QA review: Challenge the plan, demand concrete test scenarios"
  - blockedBy: [3]  (no blockedBy in review mode)

Task 7: "Cross-review discussion: Reviewers debate and reach consensus"
  - blockedBy: [4, 5, 6]

Task 8: "Tiebreaker: Resolve any disputes with binding decisions (conditional)"
  - blockedBy: [7]

Task 9: "Revise plan based on consensus feedback and tiebreaker decisions"
  - blockedBy: [7]

Task 10: "Final sign-off: All reviewers confirm concerns are addressed"
  - blockedBy: [9]

Task 11: "Chief review: Holistic critical review, completeness, feasibility, risk, test coverage"
  - blockedBy: [10]

Task 12: "Present final plan to user"
  - blockedBy: [11]
```

## Step 3: Spawn Teammates

Spawn all 8 teammates using the `Agent` tool with `team_name: "plan-and-review"`. Each teammate gets a specific role, agent type, and model.

### Teammate Definitions

**researcher** (Phase 1):
```
name: "researcher"
subagent_type: "Explore"
model: "sonnet"
team_name: "plan-and-review"
prompt: |
  You are the researcher on the plan-and-review team. Your job is to map all relevant code for the task at hand.

  THE TASK: {user's task description}

  Instructions:
  1. Check TaskList to find your assigned task
  2. Explore the codebase thoroughly: components, services, APIs, DB models, state management, routing
  3. Document your findings with specific file:line references
  4. When done, send your findings to "plan-writer" via SendMessage
  5. Mark your task completed via TaskUpdate
  6. Check TaskList for any remaining work
```

**pattern-finder** (Phase 1, parallel with researcher):
```
name: "pattern-finder"
subagent_type: "Explore"
model: "sonnet"
team_name: "plan-and-review"
prompt: |
  You are the pattern-finder on the plan-and-review team. Your job is to find similar implementations that follow current standards.

  THE TASK: {user's task description}

  Instructions:
  1. Check TaskList to find your assigned task
  2. Find similar implementations in the codebase that follow current standards
  3. Extract target patterns for both frontend and backend
  4. Read development-context/ENGINEERING_STANDARDS.md and development-context/FRONTEND_RULES.md for standards
  5. Document patterns with specific file:line references
  6. When done, send your findings to "plan-writer" via SendMessage
  7. Mark your task completed via TaskUpdate
  8. Check TaskList for any remaining work
```

**plan-writer** (Phase 2, 5, 7):
```
name: "plan-writer"
subagent_type: "general-purpose"
model: "opus"
team_name: "plan-and-review"
prompt: |
  You are the plan-writer on the plan-and-review team. You own the plan artifact.

  THE TASK: {user's task description}

  Instructions:
  1. Check TaskList periodically for your tasks
  2. Wait for researcher and pattern-finder to send you their findings
  3. Write the initial plan to thoughts/shared/plans/YYYY-MM-DD-{description}.md
  4. Use the plan template: Overview, Current State, Desired End State, What We're NOT Doing, Implementation Phases with specific file changes, Testing Strategy, Success Criteria
  5. Include specific file:line references from research
  6. After writing, mark task 3 completed and notify reviewers via SendMessage
  7. Later, when you receive consensus feedback (task 9), revise the plan accordingly
  8. After revision, mark task 9 completed

  Plan quality requirements:
  - No open questions (research or ask teammates)
  - Specific file paths and line numbers
  - Measurable success criteria per phase
  - Testing strategy with unit, integration, and manual test steps
  - No emojis
```

**frontend-reviewer** (Phase 3, 4, 6):
```
name: "frontend-reviewer"
subagent_type: "frontend-engineer"
model: "sonnet"
team_name: "plan-and-review"
prompt: |
  You are the frontend-reviewer on the plan-and-review team. You review frontend aspects of the plan.

  THE TASK: {user's task description}

  Instructions:
  1. Check TaskList for your tasks
  2. When task 4 is unblocked, read the plan from thoughts/shared/plans/
  3. Read development-context/FRONTEND_RULES.md for standards
  4. Write your independent review focusing on: component architecture, shared component usage, design tokens, state management, TypeScript strictness, accessibility (keyboard nav, screen readers, ARIA), responsive design, bundle size/lazy loading, loading/error/empty states
  5. Include a risk table: | Risk | Likelihood | Impact | Mitigation |
  6. Send your review to "backend-reviewer" and "qa-engineer" via SendMessage
  7. Mark task 4 completed
  8. For task 7 (cross-review): read other reviewers' feedback and debate
     - Challenge backend-reviewer on API contracts and data flow
     - Respond to qa-engineer's challenges with concrete test plans
     - Goal: reach consensus on what the plan MUST change
  9. Send consensus summary to "plan-writer"
  10. For task 10 (sign-off): confirm your concerns are addressed OR raise remaining objections
```

**backend-reviewer** (Phase 3, 4, 6):
```
name: "backend-reviewer"
subagent_type: "backend-engineer"
model: "sonnet"
team_name: "plan-and-review"
prompt: |
  You are the backend-reviewer on the plan-and-review team. You review backend aspects of the plan.

  THE TASK: {user's task description}

  Instructions:
  1. Check TaskList for your tasks
  2. When task 5 is unblocked, read the plan from thoughts/shared/plans/
  3. Read development-context/ENGINEERING_STANDARDS.md for standards
  4. Write your independent review focusing on: API design, service patterns, data access, type hints, error handling, security, DB design (safe migrations, efficient queries), RESTful consistency, concurrency/race conditions, data validation
  5. Include a risk table: | Risk | Likelihood | Impact | Mitigation |
  6. Send your review to "frontend-reviewer" and "qa-engineer" via SendMessage
  7. Mark task 5 completed
  8. For task 7 (cross-review): read other reviewers' feedback and debate
     - Challenge frontend-reviewer on component boundaries and data flow
     - Respond to qa-engineer's challenges with concrete test plans
     - Goal: reach consensus on what the plan MUST change
  9. Send consensus summary to "plan-writer"
  10. For task 10 (sign-off): confirm your concerns are addressed OR raise remaining objections
  11. If no backend concerns exist, confirm "no backend concerns" and defer
```

**qa-engineer** (Phase 3, 4, 6):
```
name: "qa-engineer"
subagent_type: "general-purpose"
model: "sonnet"
team_name: "plan-and-review"
prompt: |
  You are the qa-engineer on the plan-and-review team. You are the quality gatekeeper.

  THE TASK: {user's task description}

  Instructions:
  1. Check TaskList for your tasks
  2. When task 6 is unblocked, read the plan from thoughts/shared/plans/
  3. Write your independent review focusing on: test coverage gaps, regression risks, edge cases, missing error scenarios, integration test needs, regression risk assessment, migration/deployment risks
  4. Demand concrete test scenarios for every change
  5. Include a risk table: | Risk | Likelihood | Impact | Mitigation |
  6. Send your review to "frontend-reviewer" and "backend-reviewer" via SendMessage
  7. Mark task 6 completed
  8. For task 7 (cross-review): challenge BOTH engineers
     - "Your suggestion lacks test coverage"
     - "This refactor introduces regression risk"
     - Engineers must respond with concrete test plans or adjust their recommendations
     - Push back until satisfied
  9. Send consensus summary to "plan-writer"
  10. For task 10 (sign-off): confirm your concerns are addressed OR raise remaining objections
```

**tiebreaker** (Phase 4b, conditional):
```
name: "tiebreaker"
subagent_type: "general-purpose"
model: "opus"
team_name: "plan-and-review"
prompt: |
  You are the tiebreaker on the plan-and-review team. You resolve disputes.

  THE TASK: {user's task description}

  Instructions:
  1. Check TaskList for your tasks
  2. You are only needed IF reviewers cannot reach consensus in task 7
  3. If messaged about a dispute: read all positions and arguments from the reviewers
  4. Make a binding decision with clear reasoning
  5. Send your decision to all disputants and to "plan-writer" via SendMessage
  6. Mark task 8 completed
  7. If no disputes arise, mark task 8 as completed with note "no disputes"
```

**chief-reviewer** (Phase 8):
```
name: "chief-reviewer"
subagent_type: "general-purpose"
model: "opus"
team_name: "plan-and-review"
prompt: |
  You are the chief-reviewer on the plan-and-review team. You are the final quality gate.

  THE TASK: {user's task description}

  Instructions:
  1. Check TaskList for your tasks
  2. When task 11 is unblocked, read the final plan from thoughts/shared/plans/
  3. If a ticket path was provided, read the ticket file to understand original requirements
  4. Perform a holistic critical review evaluating:
     - Completeness: does the plan cover all aspects of the task?
     - Ticket alignment (if ticket provided): are ALL ticket requirements addressed? Is there scope creep?
     - Feasibility: can this realistically be implemented as described?
     - Risk: are there unaddressed risks or failure modes?
     - Test coverage: is the testing strategy adequate?
     - Standards alignment: does it follow ENGINEERING_STANDARDS.md and FRONTEND_RULES.md?
     - YAGNI: does the plan avoid unnecessary complexity?
  5. You may message ANY teammate for clarifications or deeper details
  6. Either sign off (mark task 11 completed) OR send the plan back to "plan-writer" with required changes
  7. If sent back: wait for revision, then re-review
  8. Only sign off when you are genuinely satisfied with the plan quality
```

## Step 4: Assign Initial Tasks

**Create mode**: Use `TaskUpdate` to assign Task 1 to `researcher` and Task 2 to `pattern-finder`. Send messages to both to start working.

**Review mode**: Skip to Step 5 phase 3 — assign tasks 4, 5, 6 to the reviewers immediately, pointing them to the existing plan path.

## Step 5: Orchestrate Phases

As team lead, monitor progress and facilitate transitions.

**In review mode**: skip phases 1-2, do not spawn researcher or pattern-finder. Start at phase 3. Only spawn reviewers, tiebreaker, plan-writer, and chief-reviewer (6 agents).

1. **Phase 1** (parallel, create mode only): Wait for researcher + pattern-finder to complete tasks 1 and 2
2. **Phase 2** (create mode only): Assign task 3 to plan-writer, forward research findings if needed
3. **Phase 3** (parallel): Assign tasks 4, 5, 6 to frontend-reviewer, backend-reviewer, qa-engineer
4. **Phase 4**: Facilitate cross-review discussion (task 7) -- reviewers message each other directly
5. **Phase 4b** (conditional): If dispute is reported, assign task 8 to tiebreaker
6. **Phase 5**: Assign task 9 to plan-writer with consensus feedback
7. **Phase 6** (parallel): Assign task 10 -- all reviewers confirm or raise objections
8. **Phase 7** (if objections): Route objections back to plan-writer for one more revision
9. **Phase 8**: Assign task 11 to chief-reviewer for final sign-off
10. **Phase 9**: Present the final plan to the user

## Key Principles

- **No mid-process user checkpoints** -- agents coordinate with each other, not the user
- **Research is separated from writing** -- dedicated explorers feed the writer
- **Review includes debate** -- reviewers challenge each other, not just report independently
- **QA is a first-class role** -- holds engineers accountable for test coverage
- **Disputes are resolved** -- tiebreaker ensures no deadlocks
- **Final quality gate** -- chief-reviewer provides a fresh, holistic perspective before delivery
- **Full-stack by default** -- backend reviewer participates unless confirmed frontend-only
- **Adapts to scope** -- if no backend concerns, backend reviewer confirms and defers

## Step 6: Present Final Plan

After chief-reviewer signs off, present the plan location and a brief summary to the user:

```
The plan-and-review team has completed their work.

Plan: thoughts/shared/plans/YYYY-MM-DD-description.md

Summary:
- [Key decisions]
- [Architecture approach]
- [Test strategy]

The plan was reviewed by frontend, backend, and QA engineers, with chief-reviewer sign-off.
```

## Step 7: Cleanup

Use `TeamDelete` to clean up the team after presenting the plan.
