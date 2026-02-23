# Context Engineering Plugin

A Claude Code plugin that provides a complete development workflow with specialized agents, research commands, and execution context.

## What This Plugin Provides

The `context-engineering` plugin establishes a comprehensive development methodology:

1. **Research-First Approach**: Specialized agents for exploring codebases before making changes
2. **Plan-Driven Development**: Structured planning with agent consultation before implementation
3. **Thoughts Directory Convention**: Organized storage for tickets, plans, research, PRs
4. **Agent Delegation**: Clear separation between research, planning, and execution agents
5. **Quality Gates**: Verification checkpoints at each implementation phase

## Installation

### From Marketplace

```bash
/plugin install context-engineering@your-marketplace
```

### Local Testing

```bash
claude --plugin-dir /path/to/context-engineering
```

## Getting Started

After installing the plugin, run the setup command to initialize your project:

```
/context-engineering:setup
```

This creates:
- `.claude/context/` - Execution context files for specialized agents
- `thoughts/shared/` - Directory structure for tickets, plans, research, and PRs

## Available Agents (10)

| Agent | Purpose |
|-------|---------|
| `backend-engineer` | Backend Python/FastAPI development with testing and security focus |
| `frontend-engineer` | React/TypeScript frontend development with accessibility focus |
| `codebase-locator` | Find files and directories relevant to a feature |
| `codebase-analyzer` | Analyze how code works with file:line references |
| `codebase-pattern-finder` | Find similar implementations to model after |
| `thoughts-locator` | Discover documents in thoughts/ directory |
| `thoughts-analyzer` | Extract insights from thoughts documents |
| `code-cleanup` | Systematic code cleanup with multi-perspective analysis |
| `security-auditor` | Security vulnerability assessment with OWASP Top 10 and CVSS scoring |
| `web-search-researcher` | Research web sources for technical information |

## Available Commands (13)

### Planning & Research

| Command | Description |
|---------|-------------|
| `/setup` | Initialize project with context files and directory structure |
| `/create_plan` | Create detailed implementation plans through interactive research |
| `/research_codebase` | Document codebase as-is with parallel research agents |
| `/review_plan` | Review plans against tickets with multi-perspective analysis |
| `/iterate_plan` | Update existing plans with thorough research |

### Implementation

| Command | Description |
|---------|-------------|
| `/implement_plan` | Execute plans with agent delegation and verification |
| `/debug` | Debug issues by investigating logs, database, and git history |

### Code Review & PR

| Command | Description |
|---------|-------------|
| `/analyze_pr_feedback` | Analyze PR review feedback and prioritize fixes |
| `/describe_pr` | Generate comprehensive PR descriptions |
| `/commit` | Create commits with user approval |

### Security

| Command | Description |
|---------|-------------|
| `/security_audit` | Comprehensive security audit with multi-phase vulnerability assessment |

### Alternative AI Review

| Command | Description |
|---------|-------------|
| `/gemini_review` | Review PR using Gemini CLI for alternative perspective |
| `/gemini_plan_review` | Review plan using Gemini CLI |

## Workflow Overview

```
Ticket -> Research -> Plan -> Implement -> Test -> Commit -> PR -> Merge
```

### 1. Create a Ticket

Document the task in `thoughts/shared/tickets/YYYY-MM-DD-description.md`:
- Problem statement
- Requirements
- Acceptance criteria
- Out of scope (YAGNI)

### 2. Research the Codebase

```
/research_codebase How does the authentication system work?
```

This spawns parallel research agents to document the current implementation.

### 3. Create a Plan

```
/create_plan thoughts/shared/tickets/2025-01-15-add-auth.md
```

This creates an interactive planning session that:
- Consults appropriate agents (frontend/backend)
- Researches existing patterns
- Creates a phased implementation plan

### 4. Implement the Plan

```
/implement_plan thoughts/shared/plans/2025-01-15-add-auth.md
```

This autonomously:
- Delegates to backend-engineer and frontend-engineer agents
- Runs tests after each phase
- Updates plan progress
- Creates PR when complete

### 5. Handle PR Feedback

```
/analyze_pr_feedback 42 thoughts/shared/tickets/2025-01-15-add-auth.md thoughts/shared/plans/2025-01-15-add-auth.md
```

This analyzes reviewer feedback and categorizes issues by priority.

## Directory Structure Convention

```
project/
├── .claude/
│   └── context/
│       ├── BACKEND_EXECUTION.md
│       ├── FRONTEND_EXECUTION.md
│       ├── CODE_RESEARCH.md
│       └── WORKFLOW.md
├── thoughts/
│   └── shared/
│       ├── tickets/     # Task descriptions
│       ├── plans/       # Implementation plans
│       ├── research/    # Codebase research
│       └── prs/         # PR descriptions
└── CLAUDE.md            # Project instructions
```

## Customization

After running `/setup`, customize the generated files for your project:

### Context Files

The files in `.claude/context/` are templates. Update them with:
- Your project's specific test commands
- Coverage thresholds
- Project-specific patterns and conventions

### CLAUDE.md Integration

Add references to context files in your project's CLAUDE.md:

```markdown
## Agent-Specific Context

- **Backend Execution**: See `.claude/context/BACKEND_EXECUTION.md`
- **Frontend Execution**: See `.claude/context/FRONTEND_EXECUTION.md`
- **Code Research**: See `.claude/context/CODE_RESEARCH.md`
- **Workflow**: See `.claude/context/WORKFLOW.md`
```

## Key Principles

### Research Before Implementation

All research agents are **documentarians, not critics**. They describe what exists without suggesting improvements.

### Agent Delegation

Complex tasks are delegated to specialized agents:
- Backend changes → `backend-engineer`
- Frontend changes → `frontend-engineer`
- Code investigation → `codebase-analyzer`

### Plan-Driven Development

Every significant change follows the workflow:
1. Document requirements in a ticket
2. Research the codebase
3. Create a detailed plan with agent consultation
4. Implement with verification at each phase

### Quality Gates

- All tests must pass before proceeding to next phase
- Manual verification checkpoints at phase boundaries
- PR review analysis before merge

## Requirements

- Claude Code CLI
- Git and GitHub CLI (`gh`)
- For Gemini commands: Gemini CLI installed and configured

## License

MIT
