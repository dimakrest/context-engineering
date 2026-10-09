Target: inner PR {PR} ("{TITLE}"), open as a draft: {PR_URL}. Worktree {WT} at {HEAD_BEFORE}; the PR's base commit is {HEAD_BEFORE_CONTRACT}. Rules: before anything else read {SCRATCH}/briefs/rules-worker.md (standing rules and test limits); it binds you.

Change: run the /code-review skill on this PR's diff (`git diff {HEAD_BEFORE_CONTRACT}..HEAD`) and write the verified findings to {SCRATCH}/reviews/{PR}/claude.md. Before you review, read the contract {SCRATCH}/contracts/{PR}.md and the part about {PR} in the intent {SCRATCH}/intent/{ISSUE}.md, and add these questions to the review: does the code satisfy every contract row; did any "Must not change" item change; did the contract drift from the intent; does any test pass for a wrong implementation. Give every finding an id (R1, R2, ...), a severity (high, medium, low), file:line, the claim, a concrete failing scenario, and what it breaks: a contract row, a "Must not" item, or "generic". End the file with "FINDINGS: <n>".

Constraints: read-only: no edit, no commit, no push; the worktree's HEAD and its uncommitted files must be the same when you finish. Write the findings to the file only: do not post them on the pull request or anywhere on GitHub, whatever the skill suggests. Fix nothing. Do not run the full unit suite.

Ownership: {SCRATCH}/reviews/{PR}/claude.md.

Observable acceptance: claude.md exists and ends with its FINDINGS line (0 is a valid count). Send worker_done with --outcome succeeded and a body giving the number of findings by severity.
