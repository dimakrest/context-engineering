---
name: code-review-by-team
description: Multi-agent PR code review pipeline with cleanup, specialized reviewers, adversarial analysis (including optional Codex adversarial review), and feature alignment checking. Use when the user wants a thorough code review, says "review this PR", "review my changes", asks for feedback on their branch, or wants to know if their code is ready to merge.
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

### Step 5 preamble: Detect Codex plugin (run once)

Before launching reviewers, check whether the Codex plugin (`codex@openai-codex`) is installed. If it is, the Codex Adversarial Reviewer below will be dispatched alongside the others; if not, it is skipped silently and Step 7 will surface a one-line install recommendation.

```bash
CODEX_PLUGIN_DIR=$(find ~/.claude/plugins -type f -path "*/codex/scripts/codex-companion.mjs" 2>/dev/null \
  | head -1 | xargs -I {} dirname {} | xargs -I {} dirname {})

if [ -n "$CODEX_PLUGIN_DIR" ] \
   && [ -f "$CODEX_PLUGIN_DIR/scripts/codex-companion.mjs" ] \
   && [ -f "$CODEX_PLUGIN_DIR/commands/adversarial-review.md" ]; then
  CODEX_AVAILABLE=1
else
  CODEX_AVAILABLE=0
  CODEX_PLUGIN_DIR=""
fi
```

Remember `CODEX_AVAILABLE` and `CODEX_PLUGIN_DIR` for Steps 5, 6, and 7.

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

### Codex Adversarial Reviewer (only if Codex plugin installed)

Dispatch this in the SAME parallel batch as the other Step 5 reviewers (single message, multiple Agent tool calls). Skip entirely when `CODEX_AVAILABLE=0`.

Substitute the resolved absolute `$CODEX_PLUGIN_DIR` into the prompt before sending it.

```
Agent call:
- subagent_type: "general-purpose"
- prompt:
  "You are dispatching the external Codex adversarial review for a PR review pipeline.
  Codex is an out-of-process reviewer that focuses on breaking confidence in the
  change (auth, data loss, race conditions, schema drift, observability gaps).

  ## Task

  1. Run this exact bash command and wait for it to complete (it may take several minutes):

     node \"$CODEX_PLUGIN_DIR/scripts/codex-companion.mjs\" adversarial-review --wait

     The `--wait` flag forces foreground execution and skips Codex's interactive
     'wait or run in background' prompt. Do not change the flag, do not switch
     to `--background`, do not call `/codex:status` or `/codex:result` — just
     block on this single command.

  2. Capture the full stdout verbatim. Do NOT paraphrase, summarize, reformat,
     or trim it. Codex returns structured findings; the deep-analyzer needs the
     raw output.

  3. If the command exits non-zero or returns no findings, report:
     'Codex adversarial review failed' followed by the captured stderr (last
     ~50 lines). Do not retry — the orchestrator will treat this as a missing
     reviewer.

  4. Return ONLY the verbatim Codex stdout (or the failure note above). No
     preamble, no commentary."
```

## Step 6: Deep Analysis & Artifact Generation

Do NOT compile the report yourself. After ALL Step 5 reviewers complete, collect every reviewer's full output verbatim and dispatch a single **deep-analyzer** agent. It verifies each finding is real, assesses impact, deduplicates, and writes two persistent artifacts (Markdown + HTML).

```
Agent call:
- subagent_type: "general-purpose"
- model: "opus"
- prompt:
  "You are the deep-analysis agent for a multi-reviewer PR review pipeline.
  Your job: take raw reviewer findings, verify each one is real, assess impact,
  deduplicate aggressively, and produce two artifacts (Markdown + HTML).

  ## Inputs

  ### Raw reviewer outputs (verbatim, do not paraphrase before analysis)
  {Backend reviewer output}
  {Frontend reviewer output}
  {QA reviewer output}
  {Security reviewer output}
  {Devil's Advocate reviewer output}
  {Feature Alignment reviewer output}
  {Codex Adversarial reviewer output, or "(Codex plugin not installed — skipped)" when CODEX_AVAILABLE=0}

  ### PR metadata
  - Base branch: origin/$BASE
  - Changed files: {list from Step 1}
  - Commit messages: output of `git log origin/$BASE..HEAD --oneline`
  - Cleanup applied: {/simplify summary} / {code-cleanup summary}

  ## Process

  1. **Parse** every finding from every reviewer into a structured list:
     (source_reviewer, file, line, severity, description, suggested_fix).

  2. **Deduplicate** — group findings by (file, line-range, root-cause).
     Merge duplicates into a single entry; record ALL reviewers that flagged it.

  3. **Verify** — batch related findings and spawn up to 5 sub-subagents in
     parallel (one Agent call per batch, each with subagent_type: 'general-purpose'
     and model: 'sonnet'). Each sub-subagent must:
       - Read the cited file(s) at the cited lines
       - Trace the code path / call sites with grep
       - Confirm the issue is real and reachable from actual entry points
       - Return one of: VERIFIED | DISPUTED | NOT_REPRODUCIBLE, with a one-line reason

     Drop NOT_REPRODUCIBLE findings (record them in 'Dropped Findings' with the
     sub-subagent's reason). Mark DISPUTED findings and include the dispute reason
     in the report so the human reviewer can adjudicate.

  4. **Assess impact** — for each VERIFIED finding, write 1-2 sentences on:
       - What breaks (data loss, wrong output, crash, security exposure, etc.)
       - When it triggers (always, under load, on edge input, etc.)
       - Blast radius (single user, all users, downstream services)

  5. **Re-categorize** by impact, not by reviewer's original label:
       - Must Fix: real bugs, security issues, data loss risks
       - Should Fix: maintainability/quality issues with clear cost
       - Consider: optional, low-impact suggestions

     Drop pure style nits and over-engineering suggestions (extra abstraction
     layers, premature optimization).

  6. **Generate artifacts** — write BOTH files using the Write tool. Create the
     `thoughts/shared/reviews/` directory first if it does not exist (use Bash:
     `mkdir -p thoughts/shared/reviews`). Filename uses today's date and the
     branch name: `{YYYY-MM-DD}-{branch-slug}-review.{md,html}`.

     a) Markdown: `thoughts/shared/reviews/{YYYY-MM-DD}-{branch-slug}-review.md`
        Sections, in order:
          - Summary (one paragraph: PR intent + overall quality)
          - Verdict (Ready to merge | Needs changes, with counts)
          - Must Fix
          - Should Fix
          - Consider
          - Test Coverage (QA narrative)
          - Security (security summary)
          - Feature Alignment (intent, tangential changes, better alternatives)
          - Cleanup Applied (/simplify + code-cleanup summaries)
          - Disputed Findings (with dispute reason)
          - Dropped Findings (with NOT_REPRODUCIBLE reason)

        Each finding entry must include: **[file:line]**, description, **Impact:**,
        **Suggested fix:**, **Verification:** (what the sub-subagent checked),
        **Source reviewers:** (chips of reviewer names).

     b) HTML: `thoughts/shared/reviews/{YYYY-MM-DD}-{branch-slug}-review.html`
        Self-contained single file. Constraints:
          - Inline CSS, no external stylesheets, no JS frameworks
          - System font stack (-apple-system, Segoe UI, Roboto, sans-serif)
          - Dark-mode-friendly palette (use `prefers-color-scheme: dark` media query)
          - Sticky header showing verdict + counts (Must / Should / Consider)
          - One collapsible <details> block per category and per narrative section
          - Each finding rendered as a card: monospace file:line, severity badge
            (color-coded), impact paragraph, suggested fix (in <pre> if code),
            verification line, source-reviewer chips
          - Vanilla <details>/<summary> for collapsibles — no JavaScript required

  7. **Return** to the orchestrator:
       - The two absolute file paths
       - Counts: must_fix / should_fix / consider / disputed / dropped
       - A one-paragraph executive summary of the PR's quality and verdict

  Do NOT print the full findings list in your return — the artifacts are the
  source of truth. The orchestrator only needs the summary and paths."
```

Wait for the deep-analyzer to complete before proceeding.

## Step 7: Present to User

Print a short chat summary using the deep-analyzer's return values. Do NOT re-list every finding inline — the artifacts are the source of truth.

```
## PR Review Complete

{one-paragraph executive summary from deep-analyzer}

**Verdict:** {Ready to merge | Needs changes}
**Findings:** {N must-fix} / {M should-fix} / {K consider}  ({D disputed}, {X dropped})

**Reports:**
- Markdown: `thoughts/shared/reviews/{date}-{branch}-review.md`
- HTML:     `thoughts/shared/reviews/{date}-{branch}-review.html`

To open the HTML report:
`open thoughts/shared/reviews/{date}-{branch}-review.html`
```

When `CODEX_AVAILABLE=0`, append this blockquote to the summary so the user knows an extra reviewer is available:

```
> **Tip:** Install the Codex plugin to add an out-of-process adversarial reviewer to this pipeline. Once installed, `/code-review` will automatically include it.
> - `/plugin marketplace add openai/codex`
> - `/plugin install codex@openai-codex`
> Requires the `codex` CLI on PATH (`brew install codex` or see openai/codex).
```

Do not add this tip when Codex was used in this run.
