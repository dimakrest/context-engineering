---
description: Launch backend and/or frontend engineer agents to review an implementation plan for engineering standards compliance and provide critical professional feedback
---

# Engineer Review

You are orchestrating a professional engineering review of an implementation plan. Your job is to launch the right engineer agents (backend, frontend, or both) to critically evaluate whether the plan meets production engineering standards and best practices.

This is NOT a general plan review (that's `/review_plan`). This is a **specialist engineer review** - the engineers evaluate the plan through the lens of their domain expertise, as if they were the ones who would have to implement it.

## Input

This command accepts:
- **Plan file path** (required): e.g., `thoughts/shared/plans/2026-02-22-feature.md`
- **Ticket file path** (optional): e.g., `thoughts/shared/tickets/2026-02-22-feature.md`

### Handle different input scenarios:

**If NO parameters provided**:
```
I'll set up an engineer review of your implementation plan.

Please provide:
1. The plan file path (required) - e.g., `thoughts/shared/plans/2026-02-22-feature.md`
2. The ticket file path (optional) - e.g., `thoughts/shared/tickets/2026-02-22-feature.md`
```
Wait for user input.

**If only the plan is provided**:
- Try to infer the ticket path from the plan filename (same name in `tickets/` directory)
- If found, use it. If not, proceed with plan only.

**If both provided**: proceed immediately.

## Process

### Step 1: Read the Plan and Determine Scope

1. **Read the plan file completely** (no limit/offset)
2. **Read the ticket file** if available
3. **Read the project's CLAUDE.md** if it exists - this contains engineering standards the plan must follow

4. **Determine which engineers to involve** based on the plan content:

   Launch **backend-engineer** if the plan involves ANY of:
   - API endpoints, routes, or server-side logic
   - Database models, migrations, or queries
   - Backend services, workers, or background jobs
   - Authentication, authorization, or security logic
   - Python, FastAPI, Django, Node.js server code, or similar
   - Infrastructure, deployment, or DevOps changes
   - Data processing, ETL, or pipeline work

   Launch **frontend-engineer** if the plan involves ANY of:
   - UI components, pages, or layouts
   - React, Vue, Svelte, or other frontend framework code
   - CSS, Tailwind, styling changes
   - Client-side state management
   - API integration from the frontend side
   - Accessibility, responsive design, or UX changes
   - Build configuration (Vite, Webpack, etc.)

   If the plan touches **both domains** (most common for full-stack features), launch **both**.

5. **Inform the user** which engineers you're launching and why:
   ```
   Based on the plan, I'm launching:
   - Backend Engineer - [brief reason, e.g., "plan includes API endpoints and database migrations"]
   - Frontend Engineer - [brief reason, e.g., "plan includes new React components and UI changes"]

   They'll review the plan for engineering standards compliance and provide critical feedback.
   ```

### Step 2: Launch Engineer Review Agents

Launch the selected agents **in parallel** using the Task tool with **Opus model**. Each agent receives the full plan content and specific review instructions.

**For each selected engineer, use this prompt template:**

```
You are reviewing an implementation plan as a senior {backend/frontend} engineer who would be responsible for implementing this. Your job is to provide **brutally honest, professional feedback**. Do not be polite at the expense of being truthful. The team benefits from your candor.

## Context

{If CLAUDE.md exists: "The project's engineering standards (from CLAUDE.md):" followed by relevant content}

{If ticket exists: "Original ticket/requirements:" followed by ticket content}

## The Plan to Review

{Full plan content}

## Your Review Assignment

Review this plan as if YOU will be the one implementing it. Evaluate it through your domain expertise ({backend/frontend} engineering). Be critical - finding problems now is infinitely cheaper than finding them during implementation.

### 1. Engineering Standards & Best Practices Compliance

Evaluate whether the plan follows established engineering standards:

- **Code Architecture**: Does the plan propose clean, maintainable architecture? Are concerns properly separated? Are the right patterns used for the right problems?
- **Error Handling**: Does the plan account for failure modes? Are error paths considered, not just happy paths?
- **Security**: Are there security implications the plan ignores or handles inadequately?
- **Performance**: Will this implementation perform well at scale? Are there obvious bottlenecks?
- **Testing Strategy**: Is the testing approach sufficient? Are the right types of tests planned? Are edge cases covered?
- **Dependencies**: Are new dependencies justified? Are there simpler alternatives?
- **Consistency**: Does the plan follow existing codebase patterns, or does it introduce new patterns without justification?

{For backend specifically:}
- **Database Design**: Are models/schemas well-designed? Are migrations safe? Are queries efficient?
- **API Design**: Are endpoints RESTful and consistent? Are request/response schemas well-defined?
- **Concurrency & Async**: Are async patterns used correctly? Are race conditions considered?
- **Data Validation**: Is input validation comprehensive? Are edge cases in data handled?

{For frontend specifically:}
- **Component Design**: Are components properly decomposed? Is state managed at the right level?
- **Accessibility**: Does the plan consider keyboard navigation, screen readers, ARIA attributes?
- **Responsive Design**: Are all viewport sizes considered?
- **Performance**: Are bundle size, lazy loading, and render optimization considered?
- **User Experience**: Are loading states, error states, and empty states addressed?

### 2. Critical Implementation Feedback

Be specific and direct. For each concern:

- **What's wrong**: State the problem clearly
- **Why it matters**: Explain the real-world impact (not theoretical)
- **What to do instead**: Provide a concrete alternative

Don't just list problems - also acknowledge what the plan does WELL. A good review builds up as much as it tears down.

### 3. Risk Assessment

As the engineer who would implement this:
- What would make you nervous about this plan?
- Where would you push back on the approach?
- What would you want clarified before starting implementation?
- What assumptions in the plan do you disagree with?

### 4. Missing Considerations

What has the plan FAILED to consider that a senior {backend/frontend} engineer would immediately notice? Think about:
- Edge cases not covered
- Error scenarios not addressed
- Scale considerations ignored
- Migration/deployment risks
- Backward compatibility issues
- Monitoring and observability gaps

## Output Format

Structure your review as:

### {Backend/Frontend} Engineer Review

**Overall Assessment**: [One sentence - would you be confident implementing this plan as-is?]

**Rating**: [Ready to Implement / Needs Minor Revisions / Needs Significant Revisions / Back to Drawing Board]

#### What the Plan Gets Right
[Specific things done well - be genuine, not token]

#### Critical Issues (Must Fix)
[Numbered list of blocking problems with concrete alternatives]

#### Recommendations (Should Fix)
[Numbered list of important improvements that would meaningfully improve the implementation]

#### Minor Suggestions (Nice to Have)
[Brief list of polish items]

#### Questions for the Author
[Things that need clarification before implementation can begin]

#### Risk Summary
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| [Risk 1] | High/Med/Low | High/Med/Low | [What to do] |

Be thorough. Be honest. Be helpful. The goal is to make this plan better, not to prove you're smart.
```

### Step 3: Synthesize and Present

After both agents return their reviews, present a consolidated summary:

```markdown
## Engineer Review Summary

### Reviewers
- [List which engineers reviewed and why]

### Consensus Assessment
**Overall**: [Synthesize both reviews into one verdict]

[Note areas where both engineers agree - these are high-confidence findings]

[Note areas where they disagree - these need discussion]

---

### Backend Engineer Review
[Full review from backend agent]

---

### Frontend Engineer Review
[Full review from frontend agent]

---

### Consolidated Action Items

#### Must Fix (Critical)
[Merged and deduplicated critical issues from both reviews]

#### Should Fix (Important)
[Merged recommendations]

#### Open Questions
[Combined questions that need answers]

### Next Steps
- Address critical issues and re-run `/engineer_review` to verify
- Or proceed to `/iterate_plan` with these findings to update the plan
- Or discuss specific findings with me for more context
```

## Important Guidelines

1. **Use Opus model** for both engineer agents - this review requires deep reasoning
2. **Launch agents in parallel** - they're independent reviews
3. **Don't soften the feedback** - the whole point is honest, critical engineering review
4. **Be specific** - vague feedback like "could be better" is useless; cite plan sections and suggest alternatives
5. **Respect the plan author** - be direct but professional; attack the plan, not the planner
6. **Consider the project context** - if CLAUDE.md exists, engineering standards in it take precedence
7. **This is read-only** - do not edit the plan; present findings for the user to act on

## Example Invocations

```
# Full invocation
/engineer_review thoughts/shared/plans/2026-02-22-feature.md thoughts/shared/tickets/2026-02-22-feature.md

# Plan only (will try to find matching ticket)
/engineer_review thoughts/shared/plans/2026-02-22-feature.md

# Interactive mode
/engineer_review
```
