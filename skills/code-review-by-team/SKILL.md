---
name: code-review-by-team
description: Multi-agent PR code review pipeline with cleanup, specialized reviewers, adversarial analysis, and feature alignment checking. Use when the user wants a thorough code review, says "review this PR", "review my changes", asks for feedback on their branch, or wants to know if their code is ready to merge.
user_invocable: true
---

# /code-review -- PR Review Team

Coordinates a multi-phase review pipeline: first cleans up the code, then dispatches specialized reviewers for honest, professional, and concise feedback. Focuses on actionable findings -- no nit-picking, no over-engineering suggestions.

## Step 1: Gather PR Changes

Detect the base branch first, then use it consistently for all diffs:

```bash
# Detect the base branch (usually main or master)
BASE=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "main")

# Ensure base branch is up to date before diffing
git fetch origin "$BASE"

# Get list of changed files (three-dot diff shows only changes introduced by the branch)
git diff "origin/$BASE...HEAD" --name-only

# Get the full diff
git diff "origin/$BASE...HEAD"

# Get commit messages for intent context
git log "origin/$BASE..HEAD" --oneline
```

Use `origin/$BASE` (the detected base branch) in all subsequent git diff and git log commands throughout this skill — not a hardcoded branch name.

**CRITICAL**: Always use three-dot diffs (`origin/$BASE...HEAD`), NOT two-dot (`origin/$BASE HEAD`). Three-dot diffs show only changes introduced by the branch (from the merge-base to HEAD). Two-dot diffs compare branch tips directly, which inflates the review scope when the branch has merged the base into it.

## Step 2: Understand Project Context

Before dispatching agents, gather project context:
- Check for project guidelines: `.claude/CLAUDE.md`, `CONTRIBUTING.md`, `.editorconfig`, linter configs
- Check for engineering standards docs referenced in CLAUDE.md
- Identify the tech stack from changed file extensions and imports
- Note any project-specific patterns, conventions, or agent definitions

## Step 3: Categorize Changes

Sort changed files into review domains:

- **Backend**: Server-side code (Python, Go, Java, Node.js, etc.) excluding DB migrations and test files
- **Frontend**: Client-side code (TSX, TS, JSX, JS, CSS, SCSS, etc.) excluding test files
- **Tests**: `*test*`, `*spec*` files, test directories
- **Security-sensitive**: auth files, config files, API endpoints, input handling, external URL fetching

Skip domains with no changed files.

## Step 4: Code Cleanup Phase (sequential)

Before reviewers look at the code, run cleanup agents to improve code quality. These agents make actual changes to the code.

### Step 4a: Run /simplify

Launch a dedicated agent to run the built-in `/simplify` skill. This is the lighter, first pass.

```
Agent call:
- subagent_type: "general-purpose"
- prompt:
  "You are running the /simplify skill on PR changes as part of a code review pipeline.

  ## Changed Files
  {list of ALL changed files}

  ## Instructions

  1. Run the /simplify skill using the Skill tool (skill: 'simplify')
  2. Let it analyze and fix the changed code
  3. Report back what changes were made (if any)

  If /simplify makes no changes, report 'No simplification changes needed.'"
```

Wait for this agent to complete before proceeding.

### Step 4b: Run Code Cleanup

After /simplify finishes, launch the code-cleanup agent for a deeper pass.

```
Agent call:
- subagent_type: "context-engineering:code-cleanup"
- prompt:
  "You are running a code cleanup pass on PR changes as part of a code review pipeline. The /simplify skill has already made a first pass on this code.

  First, check for project-specific guidelines:
  - Read `.claude/CLAUDE.md` if it exists for project rules
  - Read any engineering standards docs referenced in CLAUDE.md

  ## Changed Files
  {list of ALL changed files}

  Run `git diff origin/$BASE...HEAD -- <file>` for each file to see the current state of changes.

  ## Instructions

  Focus your cleanup on the changed files only. Apply your full five-phase workflow (Analyze, Plan, Execute, Validate, Report) but scoped to these files.

  Priorities:
  1. Security issues (hardcoded secrets, injection risks)
  2. Dead code and unused imports introduced by the PR
  3. Structural improvements within the changed code
  4. Code duplication within the PR

  Do NOT:
  - Touch files outside the PR diff
  - Make large-scale refactors beyond the PR scope
  - Break existing tests

  After making changes, run existing tests to validate. Report what you changed and why."
```

Wait for this agent to complete before proceeding.

## Step 5: Dispatch Review Agents (in parallel)

Launch ALL relevant review agents simultaneously. Each reviewer works independently on the cleaned-up code.

### Backend Reviewer (if backend files changed)

```
Agent call:
- subagent_type: "context-engineering:backend-engineer"
- prompt:
  "Review this PR's backend changes. Scope your review to changes introduced by this PR only.

  ## Changed Files
  {list of changed backend files}

  Run `git diff origin/$BASE...HEAD -- <file>` for each file to see the exact changes. Read full files when needed for context.
  Read commit messages (`git log origin/$BASE..HEAD --oneline`) to understand intent.

  ## Output Format

  Report ONLY substantive findings. Skip anything that passes. Do not invent issues.

  For each finding:
  - **[file:line]** -- Description of the issue
  - **Why it matters** -- One sentence on impact
  - **Suggested fix** -- Brief code suggestion if helpful

  Categorize as:
  - **Must Fix**: Bugs, security issues, pattern violations that will cause problems
  - **Should Fix**: Code quality issues, maintainability concerns
  - **Consider**: Optional improvements worth discussing"
```

### Frontend Reviewer (if frontend files changed)

```
Agent call:
- subagent_type: "context-engineering:frontend-engineer"
- prompt:
  "Review this PR's frontend changes. Scope your review to changes introduced by this PR only.

  ## Changed Files
  {list of changed frontend files}

  Run `git diff origin/$BASE...HEAD -- <file>` for each file to see the exact changes. Read full files when needed for context.
  Read commit messages (`git log origin/$BASE..HEAD --oneline`) to understand intent.

  ## Output Format

  Report ONLY substantive findings. Skip anything that passes. Do not invent issues.

  For each finding:
  - **[file:line]** -- Description of the issue
  - **Why it matters** -- One sentence on impact
  - **Suggested fix** -- Brief code suggestion if helpful

  Categorize as:
  - **Must Fix**: Bugs, accessibility issues, design system violations
  - **Should Fix**: Code quality, missing shared component usage, type issues
  - **Consider**: Optional improvements worth discussing"
```

### QA Reviewer (if any test files changed OR if non-test code changed without corresponding tests)

```
Agent call:
- subagent_type: "general-purpose"
- prompt:
  "You are a QA engineer reviewing test coverage for a PR.

  ## Your Review Mindset

  Think like a QA engineer who cares about testability:
  - Are the right things being tested?
  - Are edge cases and error paths covered?
  - Are tests actually testing behavior (not implementation details)?
  - Is there production code that changed without corresponding test updates?

  ## Changed Files
  {list of ALL changed files, both test and non-test}

  Run `git diff origin/$BASE...HEAD -- <file>` for each file to see the exact changes.

  ## Review Focus

  **Test Coverage Gaps**:
  - Which changed production files lack corresponding test changes?
  - Are new functions/endpoints/components untested?
  - Are error paths and edge cases covered?

  **Test Quality**:
  - Do tests verify behavior or just implementation?
  - Are test names descriptive and clear?
  - Are assertions meaningful?
  - Is test setup minimal and focused?
  - Any redundant or duplicate test cases?

  **Missing Tests**:
  - Suggest specific test cases that should be added
  - Focus on high-value tests (happy path + most likely failure modes)
  - Do NOT suggest exhaustive edge-case testing for simple code

  ## Output Format

  **Coverage Summary**:
  - Files with tests: {count}
  - Files missing tests: {list}

  **Findings** (only if substantive):
  - **Gap**: [file] -- What is not tested and why it matters
  - **Quality Issue**: [test_file:line] -- What is wrong with the test
  - **Suggested Test**: Brief description of a test worth adding

  Be pragmatic. Not everything needs a test. Focus on code paths that could break."
```

### Security Reviewer (always runs)

```
Agent call:
- subagent_type: "context-engineering:security-auditor"
- prompt:
  "Review this PR's diff for security issues. This is a PR-scoped review, NOT a full security audit — only report issues INTRODUCED or AFFECTED by this PR.

  ## Changed Files
  {full list of changed files}

  Run `git diff origin/$BASE...HEAD` to see the full diff.
  Read commit messages (`git log origin/$BASE..HEAD --oneline`) to understand intent.

  Do NOT report pre-existing issues, theoretical vulnerabilities without evidence in the diff, or general recommendations not tied to specific changes.

  ## Output Format

  For each finding:
  - **Severity**: CRITICAL / HIGH / MEDIUM / LOW
  - **[file:line]** -- Description
  - **Attack scenario** -- One sentence on how this could be exploited
  - **Fix** -- Brief remediation

  Categorize as:
  - **Must Fix**: CRITICAL/HIGH severity
  - **Should Fix**: MEDIUM severity
  - **Consider**: LOW severity

  If no security issues found, say 'No security issues identified in this PR' and briefly note what you checked."
```

### Devil's Advocate Reviewer (always runs)

```
Agent call:
- subagent_type: "general-purpose"
- prompt:
  "You hate this implementation. Your job is to find real, verified problems — not style preferences.

  ## Changed Files
  {list of ALL changed files}

  Run `git diff origin/$BASE...HEAD` to see the full diff.
  Read commit messages (`git log origin/$BASE..HEAD --oneline`) to understand intent.

  ## Process

  1. Read the diff and form your harshest critique — what could break, what's fragile, what's wrong
  2. Batch your findings and verify them using up to 5 subagents (group related issues per subagent, use model: "sonnet" for each):
     - Edge case? Have the subagent trace the code path and confirm it's actually reachable
     - Race condition? Have the subagent check if concurrency is actually possible in this context
     - Missing validation? Have the subagent check if it's handled upstream
     - Wrong approach? Have the subagent find how similar problems are solved in this codebase
  3. Drop anything the subagent disproves. Only report verified issues.

  ## What to look for

  - Assumptions that break under load, concurrency, or unexpected input
  - Edge cases in the changed logic (nulls, empty collections, boundary values, unicode, timezone)
  - Error paths that silently swallow failures or leave state inconsistent
  - Coupling or design choices that will make the next change painful
  - Things that work now but will break when requirements inevitably shift

  ## What to skip

  - Style, naming, formatting — not your problem
  - Theoretical issues you can't verify from the code
  - Anything already covered by type system or framework guarantees

  ## Output Format

  For each VERIFIED finding:
  - **[file:line]** -- What's wrong
  - **Verification** -- What the subagent checked and confirmed
  - **Impact** -- What breaks and when
  - **Suggested fix** -- Brief, if you have one

  Categorize as:
  - **Must Fix**: Will cause bugs, data loss, or security issues
  - **Should Fix**: Fragile code that will bite someone soon
  - **Consider**: Design concerns worth discussing

  If the implementation is actually solid, say so. Don't manufacture problems."
```

### Feature Alignment Reviewer (always runs)

```
Agent call:
- subagent_type: "general-purpose"
- prompt:
  "Review every change in this PR for feature alignment and intent clarity.

  ## Changed Files
  {list of ALL changed files}

  Run `git diff origin/$BASE...HEAD` to see the full diff.
  Read commit messages (`git log origin/$BASE..HEAD --oneline`) to understand the feature intent.

  ## Process

  1. Determine what feature/goal this PR is trying to accomplish from commit messages and the diff
  2. Group meaningful changes (not trivial reformats) and spawn up to 5 subagents (use model: "sonnet" for each) to verify them in batches. Each subagent should:
     - Read the surrounding code and understand the before/after context
     - Check how similar things are done elsewhere in the codebase
     - Determine if there's an existing pattern or utility that could be used instead
  3. Annotate every change with your assessment

  ## For each change, answer:

  - **What it does** -- One sentence explaining the change in plain language
  - **Why it's needed** -- How it serves the feature goal (or doesn't)
  - **Alignment** -- Does this change directly serve the feature, or is it tangential/unnecessary?
  - **Could it be done better?** -- Based on what the subagent found about existing codebase patterns

  ## Output Format

  ### Feature Intent
  One paragraph summarizing what this PR is trying to accomplish.

  ### Change-by-Change Review

  For each file (or logical group of changes):

  **[file:lines]** -- {what changed}
  - **Purpose**: Why this change exists
  - **Alignment**: Direct / Supportive / Tangential / Unnecessary
  - **Alternative**: {better approach if one exists, with codebase evidence from subagent}

  ### Summary
  - Changes that directly serve the feature: {count}
  - Changes that could be done better: {list}
  - Changes that seem unnecessary for this feature: {list, if any}
  - Overall assessment: is the PR focused and well-scoped?"
```

## Step 6: Compile Review Report

After ALL reviewers complete, compile a unified report. Do NOT just concatenate agent outputs -- synthesize them.

### Report Format

All reviewer findings flow into a single prioritized list — don't silo findings by reviewer. The source tag shows where each finding came from.

```
## PR Review Report

### Summary
One paragraph: what the PR does, overall quality assessment, and whether it's ready to merge.

### Code Cleanup Applied
Summary of changes made by /simplify and code-cleanup agents before review:
- /simplify: {changes or "no changes needed"}
- Code cleanup: {changes or "no changes needed"}

### Must Fix (blocking merge)
- [file:line] -- Issue description (Source: Backend/Frontend/Security/QA/Devil's Advocate)

### Should Fix (strongly recommended)
- [file:line] -- Issue description (Source: reviewer name)

### Consider (optional, non-blocking)
- [file:line] -- Suggestion (Source: reviewer name)

### Test Coverage
Brief QA summary — coverage gaps and suggested additions.

### Security
Brief security summary — issues found or clean bill of health.

### Feature Alignment
The Feature Alignment reviewer produces a narrative, not a findings list. Summarize it here:
- Feature intent (one sentence)
- Changes that are tangential or unnecessary for this feature
- Better alternatives found via codebase pattern search
- Overall: is the PR focused and well-scoped?

### Verdict
- [ ] Ready to merge (no Must Fix items)
- [ ] Needs changes (Must Fix items listed above)
```

### Deduplication Rules

- If multiple reviewers flag the same issue, report it once and note which reviewers caught it
- Merge related findings into a single item when they share the same root cause
- Drop findings that contradict each other after analyzing which reviewer is correct

### Filtering Rules

- Remove nit-picks (pure style preferences with no functional impact)
- Remove over-engineering suggestions (adding abstraction layers, premature optimization)
- Keep the report focused and actionable
