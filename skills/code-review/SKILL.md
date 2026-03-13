---
name: code-review
description: Code review a pull request
user_invocable: true
---

# /code-review -- PR Review Team

Coordinates a multi-phase review pipeline: first cleans up the code, then dispatches specialized reviewers for honest, professional, and concise feedback. Focuses on actionable findings -- no nit-picking, no over-engineering suggestions.

## Step 1: Gather PR Changes

Run these commands to understand what changed:

```bash
# Get the base branch (usually main or master)
git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "main"

# IMPORTANT: Ensure main is up to date before diffing
git fetch origin main

# Get list of changed files (three-dot diff shows only changes introduced by the branch)
git diff origin/main...HEAD --name-only

# Get the full diff
git diff origin/main...HEAD

# Get commit messages for intent context
git log origin/main..HEAD --oneline
```

**CRITICAL**: Always use `git diff origin/main...HEAD` (three dots), NOT `git diff origin/main HEAD` (two dots). Three-dot diffs show only changes introduced by the branch (from the merge-base to HEAD). Two-dot diffs compare branch tips directly, which inflates the review scope when the branch has merged main into it.

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

  Run `git diff origin/main...HEAD -- <file>` for each file to see the current state of changes.

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
  "You are a senior backend code reviewer.

  First, check for project-specific guidelines:
  - Read `.claude/CLAUDE.md` if it exists for project rules
  - Read any engineering standards docs referenced in CLAUDE.md
  - Read `.claude/agents/backend-engineer.md` if it exists for agent-specific instructions

  ## Your Review Mindset

  Think like a senior engineer reviewing a teammate's PR:
  - What was the author trying to accomplish? (read commit messages)
  - Is this the right approach for the problem?
  - Does the implementation follow our established patterns?
  - Will this be maintainable 6 months from now?

  ## Changed Files to Review
  {list of changed backend files}

  Run `git diff origin/main...HEAD -- <file>` for each file to see the exact changes. Read full files when needed for context.

  ## Review Checklist

  For each changed file, evaluate:

  **Architecture & Design**:
  - Does it follow the project's established patterns?
  - Is the responsibility correctly placed (not leaking between layers)?
  - Are dependencies injected properly?

  **Code Quality**:
  - Proper imports organization
  - Functions have type hints/annotations where the language supports them
  - No dead code or unused imports
  - DRY -- no unnecessary duplication

  **API Standards** (if applicable):
  - Consistent URL naming conventions
  - Proper HTTP status codes
  - Auth/authz on endpoints

  **Data Access** (if applicable):
  - Proper query patterns (no N+1, no SQL injection)
  - Multi-tenancy respected if applicable

  **Error Handling**:
  - Appropriate but not over-engineered
  - Meaningful error messages

  ## Output Format

  Report ONLY substantive findings. Skip anything that passes.

  For each finding:
  - **[file:line]** -- Description of the issue
  - **Why it matters** -- One sentence on impact
  - **Suggested fix** -- Brief code suggestion if helpful

  Categorize findings as:
  - **Must Fix**: Bugs, security issues, pattern violations that will cause problems
  - **Should Fix**: Code quality issues, missing type hints, maintainability concerns
  - **Consider**: Optional improvements worth discussing

  If everything looks good, say so briefly. Do not invent issues."
```

### Frontend Reviewer (if frontend files changed)

```
Agent call:
- subagent_type: "context-engineering:frontend-engineer"
- prompt:
  "You are a senior frontend code reviewer.

  First, check for project-specific guidelines:
  - Read `.claude/CLAUDE.md` if it exists for project rules
  - Read any frontend rules docs referenced in CLAUDE.md
  - Read `.claude/agents/frontend-engineer.md` if it exists for agent-specific instructions

  ## Your Review Mindset

  Think like a senior frontend engineer reviewing a teammate's PR:
  - What was the author trying to build? (read commit messages)
  - Does the component architecture make sense?
  - Is this consistent with the rest of the UI?
  - Will this be maintainable and performant?

  ## Changed Files to Review
  {list of changed frontend files}

  Run `git diff origin/main...HEAD -- <file>` for each file to see the exact changes. Read full files when needed for context.

  ## Review Checklist

  For each changed file, evaluate:

  **Component Architecture**:
  - Proper component composition and separation of concerns
  - State management is appropriate (not over-complex)
  - Follows project conventions (named vs default exports, etc.)

  **Design System**:
  - Uses shared/design-system components when available
  - Uses design tokens (no hardcoded colors, spacing, sizing)
  - Consistent with existing UI patterns

  **TypeScript** (if applicable):
  - Strict types (no `any` unless justified)
  - Proper interfaces for props and data
  - API response types match backend

  **Code Quality**:
  - No unused imports or dead code
  - No console.log statements in production code
  - DRY -- no unnecessary duplication

  **UX**:
  - Loading states handled
  - Error states handled
  - Empty states handled

  ## Output Format

  Report ONLY substantive findings. Skip anything that passes.

  For each finding:
  - **[file:line]** -- Description of the issue
  - **Why it matters** -- One sentence on impact
  - **Suggested fix** -- Brief code suggestion if helpful

  Categorize findings as:
  - **Must Fix**: Bugs, accessibility issues, design system violations
  - **Should Fix**: Code quality, missing shared component usage, type issues
  - **Consider**: Optional improvements worth discussing

  If everything looks good, say so briefly. Do not invent issues."
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

  Run `git diff origin/main...HEAD -- <file>` for each file to see the exact changes.

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
  "You are reviewing a PR diff for security issues. This is a focused PR review, NOT a full security audit.

  Read `.claude/agents/security-auditor.md` if it exists for your assessment methodology.

  ## Changed Files
  {full list of changed files}

  Run `git diff origin/main...HEAD` to see the full diff.

  ## Focus Areas (PR-scoped)

  Only review for security issues INTRODUCED or AFFECTED by this PR:

  - **Auth/AuthZ**: Missing auth checks on new endpoints, privilege escalation
  - **Injection**: SQL injection, command injection, XSS in new code
  - **Input Validation**: Unvalidated user input in new endpoints
  - **Data Exposure**: Sensitive data in responses, logs, or error messages
  - **SSRF**: External URL fetching without validation
  - **Secrets**: Hardcoded credentials, API keys in code

  ## What NOT to Report

  - Pre-existing issues not related to this PR
  - Theoretical vulnerabilities with no evidence in the diff
  - General security recommendations not tied to specific changes

  ## Output Format

  For each finding:
  - **Severity**: CRITICAL / HIGH / MEDIUM / LOW
  - **[file:line]** -- Description
  - **Attack scenario** -- One sentence on how this could be exploited
  - **Fix** -- Brief remediation

  If no security issues found, say 'No security issues identified in this PR' and briefly note what you checked."
```

## Step 6: Compile Review Report

After ALL reviewers complete, compile a unified report. Do NOT just concatenate agent outputs -- synthesize them.

### Report Format

```
## PR Review Report

### Summary
One paragraph: what the PR does, overall quality assessment, and whether it's ready to merge.

### Code Cleanup Applied
Summary of changes made by /simplify and code-cleanup agents before review:
- /simplify: {changes or "no changes needed"}
- Code cleanup: {changes or "no changes needed"}

### Must Fix (blocking merge)
- [file:line] -- Issue description (Source: Backend/Frontend/Security/QA reviewer)

### Should Fix (strongly recommended)
- [file:line] -- Issue description (Source: reviewer)

### Consider (optional, non-blocking)
- [file:line] -- Suggestion (Source: reviewer)

### Test Coverage
Brief QA summary -- coverage gaps and suggested additions.

### Security
Brief security summary -- issues found or clean bill of health.

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
