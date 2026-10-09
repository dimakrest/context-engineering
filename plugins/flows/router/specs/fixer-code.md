Target: inner PR {PR} ("{TITLE}"), triaged. Worktree {WT} at {HEAD_BEFORE}. The contract is {SCRATCH}/contracts/{PR}.md. Rules file: {RULES}. When that is a path, read it before anything else (standing rules and test limits); it binds you.

Change: do every item in {SCRATCH}/reviews/{PR}/fix-code.md, and nothing else. One commit per item, with a message that starts `fix(review): <item id>`. After each fix run the tests that cover it (`{TEST_CMD} <paths or ids>`); when all are done run the contract's gate commands. Commit with `{COMMIT_CMD}`, with every hook passing, and push this branch.

Constraints: no test file ({TEST_PATHS}) and not {TEST_CONFIG}: a script checks both. If a fix needs a test to change, do not make that fix: say so in your report. Stay inside the contract's Scope and keep every "Must not" item. No refactoring beyond the item. No GitHub write.

Ownership: production code under the paths the contract's Scope gives the implementer, and {SCRATCH}/reviews/{PR}/fix-code-done.md.

Observable acceptance: the branch is pushed, its head is the worktree's HEAD, and `git status` is clean. Write {SCRATCH}/reviews/{PR}/fix-code-done.md: its first line is `VERDICT: OK <n> of <n> items done` or `VERDICT: PARTIAL <k> of <n> items done`, and below it one row per item id with the commit and the test command with its last line, or why it was not fixed. Send worker_done with --outcome succeeded and that first line as the body. If no item could be fixed, send --outcome failed.
