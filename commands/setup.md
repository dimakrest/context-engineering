---
description: Initialize project with context-engineering context files and directory structure
---

# Project Setup

This command initializes your project with the context-engineering workflow by creating:
1. `.claude/context/` directory with execution context files
2. `thoughts/shared/` directory structure for tickets, plans, research, and PRs

## What Gets Created

### Context Files (`.claude/context/`)

- **CODE_RESEARCH.md** - Instructions for research agents
- **WORKFLOW.md** - Complete development workflow documentation
- **BACKEND_EXECUTION.md** - Instructions for backend-engineer agent
- **FRONTEND_EXECUTION.md** - Instructions for frontend-engineer agent

### Thoughts Directory Structure

```
thoughts/shared/
├── tickets/      # Task descriptions and requirements
├── plans/        # Implementation plans
├── research/     # Codebase research documents
└── prs/          # PR descriptions
```

## Steps to Execute

### Step 1: Create Directory Structure

```bash
mkdir -p .claude/context
mkdir -p thoughts/shared/tickets thoughts/shared/plans thoughts/shared/research thoughts/shared/prs
```

### Step 2: Copy Context Files from Plugin Templates

```bash
cp "${CLAUDE_PLUGIN_ROOT}/templates/context/CODE_RESEARCH.md" .claude/context/
cp "${CLAUDE_PLUGIN_ROOT}/templates/context/WORKFLOW.md" .claude/context/
cp "${CLAUDE_PLUGIN_ROOT}/templates/context/BACKEND_EXECUTION.md" .claude/context/
cp "${CLAUDE_PLUGIN_ROOT}/templates/context/FRONTEND_EXECUTION.md" .claude/context/
```

### Step 3: Create .gitkeep Files

```bash
touch thoughts/shared/tickets/.gitkeep thoughts/shared/plans/.gitkeep thoughts/shared/research/.gitkeep thoughts/shared/prs/.gitkeep
```

### Step 4: Present Summary

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
   - `/create-plan` - Create implementation plans
   - `/research-codebase` - Research your codebase
   - `/implement-plan` - Execute plans
   - `/commit` - Create commits

### Available Commands:

| Command | Purpose |
|---------|---------|
| `/create-plan` | Create detailed implementation plans |
| `/research-codebase` | Document codebase with agents |
| `/implement-plan` | Execute plans with verification |
| `/iterate_plan` | Update existing plans |
| `/describe_pr` | Generate PR descriptions |
| `/commit` | Create commits with approval |
| `/debug` | Debug issues during testing |
```

## Important Notes

- Context files are templates - customize them for your specific project
- The `thoughts/shared/` structure is a convention, not a requirement
- Coverage thresholds are suggestions - adjust based on your project needs
- Test commands should be updated to match your project's tooling
