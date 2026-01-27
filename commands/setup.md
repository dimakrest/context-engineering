---
description: Initialize project with context-engineering context files and directory structure
---

# Project Setup

This command initializes your project with the context-engineering workflow by creating:
1. `.claude/context/` directory with execution context files
2. `thoughts/shared/` directory structure for tickets, plans, research, and PRs

## What Gets Created

### Context Files (`.claude/context/`)

These files provide specialized instructions for execution agents:

- **BACKEND_EXECUTION.md** - Instructions for backend-engineer agent
- **FRONTEND_EXECUTION.md** - Instructions for frontend-engineer agent
- **CODE_RESEARCH.md** - Instructions for research agents
- **WORKFLOW.md** - Complete development workflow documentation

### Thoughts Directory Structure

```
thoughts/
├── shared/
│   ├── tickets/      # Task descriptions and requirements
│   ├── plans/        # Implementation plans
│   ├── research/     # Codebase research documents
│   └── prs/          # PR descriptions
```

## Steps to Execute

### Step 1: Create Directory Structure

```bash
mkdir -p .claude/context
mkdir -p thoughts/shared/tickets
mkdir -p thoughts/shared/plans
mkdir -p thoughts/shared/research
mkdir -p thoughts/shared/prs
```

### Step 2: Create CODE_RESEARCH.md

Write the following to `.claude/context/CODE_RESEARCH.md`:

```markdown
# Code Research Agent Instructions

**For**: codebase-locator, codebase-analyzer, codebase-pattern-finder, thoughts-locator, thoughts-analyzer

## Your Only Job: Document the Codebase AS-IS

- Document what exists with file:line references
- Explain how code works
- Map component interactions
- Identify patterns in use
- NEVER suggest improvements or fixes
- NEVER critique implementation quality
- NEVER propose changes

---

## Project Context

Refer to the project's CLAUDE.md for:
- Project description and purpose
- YAGNI guidelines (what we're NOT building)
- Architecture constraints

---

## Agent Responsibilities

| Agent | Purpose |
|-------|---------|
| `codebase-locator` | Find files/directories for a feature |
| `codebase-analyzer` | Analyze HOW code works (file:line refs) |
| `codebase-pattern-finder` | Find similar patterns to model after |
| `thoughts-locator` | Find relevant thoughts/ documents |
| `thoughts-analyzer` | Extract insights from thoughts/ |

---

## File Reading Rules

- Always read FULL files (no offset/limit unless truly necessary)
- Read multiple files in parallel when independent
- Never assume - always verify by reading
- Include precise `file:line` references

---

## Output Format

```markdown
## Analysis: [Feature]

### Entry Points
- `api/routes.ts:45` - POST /api/v1/setups endpoint

### Data Flow
1. Request at `api/routes.ts:45`
2. Service layer at `services/setup-service.ts:25`
3. Repository at `repositories/setup-repo.ts:18`

### Key Patterns
- Service Layer Pattern: Controllers -> Services -> Repositories
```

---

## Output Storage

- `thoughts/shared/research/YYYY-MM-DD-topic.md`

---

## Remember

You are a technical librarian - find and document, don't critique or improve.
```

### Step 3: Create WORKFLOW.md

Write the following to `.claude/context/WORKFLOW.md`:

```markdown
# Complete Development Workflow

```
Ticket -> Research -> Plan -> Implement -> Test -> Commit -> PR -> Merge
```

---

## Stage 1: Ticket

**Location**: `thoughts/shared/tickets/YYYY-MM-DD-description.md`

**Contents**: Problem, requirements, acceptance criteria, out-of-scope (YAGNI!)

---

## Stage 2: Research

**Command**: `/research_codebase [question]`

**Agents**: codebase-locator, codebase-analyzer, codebase-pattern-finder

**Output**: `thoughts/shared/research/YYYY-MM-DD-topic.md`

**Exit**: Current implementation understood, patterns identified

---

## Stage 3: Plan

**Command**: `/create_plan [ticket_path]`

**Process**:
1. Read ticket + research fully
2. Research codebase
3. Get design buy-in
4. Write plan with phased approach

**Output**: `thoughts/shared/plans/YYYY-MM-DD-description.md`

**Exit**: Plan approved, no open questions

---

## Stage 4: Implement

**Command**: `/implement_plan [plan_path]`

**Process**:
1. Create feature branch
2. For each phase:
   - Implement changes
   - Run tests (STOP if fail)
   - Manual verification
   - Get user confirmation

**Exit**: All features implemented, all tests passing

---

## Stage 5: Test

**Requirements**:
- All tests: 100% pass rate
- Coverage: >= 80% (configurable per project)
- E2E: 100% pass (if applicable)
- Type checks: 0 errors

---

## Stage 6: Commit

**Command**: `/commit`

**Types**: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`

**NEVER**: Commit to main, skip tests, force push

---

## Stage 7: Pull Request

**Command**: `/describe_pr`

---

## Stage 8: Merge

1. PR reviewed and approved
2. Merge to main
3. Delete feature branch

---

## When to Delegate

| Situation | Delegate To |
|-----------|-------------|
| Backend complexity | backend-engineer |
| Frontend complexity | frontend-engineer |
| Code investigation | codebase-analyzer |

---

## Key Commands

| Command | Purpose |
|---------|---------|
| `/research_codebase` | Codebase research |
| `/create_plan` | Interactive planning |
| `/implement_plan` | Execute plans |
| `/commit` | Smart commits |
| `/describe_pr` | PR descriptions |
| `/debug` | Issue investigation |
```

### Step 4: Create BACKEND_EXECUTION.md

Write the following to `.claude/context/BACKEND_EXECUTION.md`:

```markdown
# Backend Execution Agent Instructions

**For**: backend-engineer, backend implementation

## Task Completion Definition

A task is ONLY complete when ALL are true:
- All tests passing (100%)
- Coverage >= 80% (adjust per project)
- Feature branch + PR created
- No known bugs
- API documentation updated (if modifying endpoints/schemas)

**NEVER** mark complete with failing tests or missing documentation.

---

## Testing Requirements

Check your project's testing documentation for specific commands.

**Coverage check is manual**: Always verify output shows adequate coverage.

---

## API Documentation Requirements

**Documentation is part of the implementation, not an afterthought.**

### When Modifying an Existing Endpoint

- Update `summary` if endpoint purpose changed
- Update `description` if behavior/parameters/response changed
- Update `responses` dict if new error codes possible
- Update docstring Args/Returns/Raises
- Update schema field descriptions if fields changed
- Verify at API docs endpoint (e.g., `/docs`, `/redoc`)

### When Creating a New Endpoint

Every new endpoint MUST have:
- response_model
- summary (3-8 words)
- description (2-4 sentences)
- operation_id (unique snake_case)
- responses (error codes)

### REST API Conventions

- URL paths use kebab-case
- Pagination uses standard structure with `items` key
- Follow existing patterns in the codebase

---

## Git Workflow (MANDATORY)

```bash
# 1. Create feature branch
git checkout -b feature/descriptive-name

# 2. Run ALL tests before committing

# 3. Commit
git add . && git commit -m "type: description"

# 4. Push and create PR
git push -u origin feature/descriptive-name
gh pr create --title "Title" --body "Description"
```

**NEVER**: Push directly to main, skip tests, mark complete without PR.

---

## Bug Fixing Rules

- NEVER use fake data to make tests pass
- NEVER add workarounds instead of fixing root cause
- Investigate deeply with logging
- Test with different inputs
- Delegate complex issues to specialized agents

---

## Completion Checklist

- [ ] Backend tests: 100% passing
- [ ] Coverage: >= threshold
- [ ] Feature branch: created
- [ ] PR: created
- [ ] API documentation: updated (if API changes made)

---

## Database Migrations (if using Alembic)

```bash
# Create migration
alembic revision --autogenerate -m "Add column"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1

# Check status
alembic current
alembic history
```

**Best Practices**:
- Always review auto-generated migrations before applying
- Test rollbacks: `upgrade head` -> `downgrade -1` -> `upgrade head`
- Descriptive names: `add_index_to_patterns_symbol` not `update`
```

### Step 5: Create FRONTEND_EXECUTION.md

Write the following to `.claude/context/FRONTEND_EXECUTION.md`:

```markdown
# Frontend Execution Agent Instructions

**For**: frontend-engineer, frontend implementation

## Task Completion Definition

A task is ONLY complete when ALL are true:
- Unit tests passing (100%)
- E2E tests passing (100%) - if applicable
- Coverage >= 80% (adjust per project)
- TypeScript: 0 errors
- Feature branch + PR created
- No known bugs

**NEVER** mark complete with failing tests or type errors.

---

## Testing Requirements

Check your project's testing documentation for specific commands.

**E2E tests are NOT optional** - 100% pass rate required (if applicable).

---

## Git Workflow (MANDATORY)

```bash
# 1. Create feature branch
git checkout -b feature/descriptive-name

# 2. Run ALL tests before committing

# 3. Commit
git add . && git commit -m "type: description"

# 4. Push and create PR
git push -u origin feature/descriptive-name
gh pr create --title "Title" --body "Description"
```

**NEVER**: Push directly to main, skip E2E tests, accept < 100% pass rate.

---

## Bug Fixing Rules

- NEVER use mock data to make E2E tests pass
- NEVER accept partial pass rates (43%, 80%, 95%)
- Investigate with DevTools and React DevTools
- Test different inputs and edge cases
- Delegate complex issues to specialized agents

---

## Design System (if applicable)

If your project has a design system:
- Use semantic color tokens, never hardcoded colors
- Use proper font families for different contexts
- Follow the project's design documentation

---

## Code Patterns

Follow your project's engineering standards documentation for:
- Component patterns
- Hook patterns
- State management patterns

---

## Completion Checklist

- [ ] Unit tests: 100% passing
- [ ] E2E tests: 100% passing (if applicable)
- [ ] Coverage: >= threshold
- [ ] TypeScript: 0 errors
- [ ] Feature branch + PR: created

---

## Process Management

**Before starting dev server**:
```bash
# Kill any existing process on dev port
lsof -ti:5173 | xargs kill -9 2>/dev/null
npm run dev
```

**Cleanup after task**:
```bash
pkill -f vitest
```
```

### Step 6: Create .gitkeep Files

Create empty `.gitkeep` files in empty directories:

```bash
touch thoughts/shared/tickets/.gitkeep
touch thoughts/shared/plans/.gitkeep
touch thoughts/shared/research/.gitkeep
touch thoughts/shared/prs/.gitkeep
```

### Step 7: Present Summary

After creating all files, present:

```
## Setup Complete!

I've initialized your project with the context-engineering workflow.

### Created:

**Context Files** (`.claude/context/`):
- `CODE_RESEARCH.md` - Research agent instructions
- `WORKFLOW.md` - Complete workflow documentation
- `BACKEND_EXECUTION.md` - Backend execution instructions
- `FRONTEND_EXECUTION.md` - Frontend execution instructions

**Thoughts Directory** (`thoughts/shared/`):
- `tickets/` - For task descriptions
- `plans/` - For implementation plans
- `research/` - For codebase research
- `prs/` - For PR descriptions

### Next Steps:

1. **Customize context files** for your project:
   - Adjust coverage thresholds
   - Add project-specific patterns
   - Update test commands

2. **Update your CLAUDE.md** to reference these context files:
   ```markdown
   ## Agent-Specific Context

   - **Backend Execution**: See `.claude/context/BACKEND_EXECUTION.md`
   - **Frontend Execution**: See `.claude/context/FRONTEND_EXECUTION.md`
   - **Code Research**: See `.claude/context/CODE_RESEARCH.md`
   - **Workflow**: See `.claude/context/WORKFLOW.md`
   ```

3. **Start using the workflow**:
   - `/create_plan` - Create implementation plans
   - `/research_codebase` - Research your codebase
   - `/implement_plan` - Execute plans
   - `/commit` - Create commits

### Available Commands:

| Command | Purpose |
|---------|---------|
| `/create_plan` | Create detailed implementation plans |
| `/research_codebase` | Document codebase with agents |
| `/implement_plan` | Execute plans with verification |
| `/review_plan` | Review plans before implementation |
| `/iterate_plan` | Update existing plans |
| `/analyze_pr_feedback` | Analyze PR review feedback |
| `/describe_pr` | Generate PR descriptions |
| `/commit` | Create commits with approval |
| `/debug` | Debug issues during testing |
| `/gemini_review` | Get Gemini's PR review |
| `/gemini_plan_review` | Get Gemini's plan review |
```

## Important Notes

- Context files are templates - customize them for your specific project
- The `thoughts/shared/` structure is a convention, not a requirement
- Coverage thresholds are suggestions - adjust based on your project needs
- Test commands should be updated to match your project's tooling
