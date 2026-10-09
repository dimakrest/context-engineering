Target: inner PR {PR} ("{TITLE}"), open as a draft: {PR_URL}. Worktree {WT} at {HEAD_BEFORE}; the PR's base commit is {HEAD_BEFORE_CONTRACT}. The contract is {SCRATCH}/contracts/{PR}.md. Rules file: {RULES}. When that is a path, read it before anything else (standing rules and test limits); it binds you.

Change: run the /simplify skill over this PR's own changes (`git diff {HEAD_BEFORE_CONTRACT}..HEAD`). Keep only simplifications that leave behaviour the same and stay inside production files this PR already changed. On a bump PR (the contract says so), simplify only lines this PR added: nothing that is not red without the bump may enter it. Run the tests this PR added or changed (`{TEST_CMD} <paths>`), then the contract's gate commands. If you changed anything: commit with `{COMMIT_CMD}`, with every hook passing, and push this branch. If nothing is worth changing, change nothing.

Constraints: no test file ({TEST_PATHS}) and not {TEST_CONFIG}: a script checks both. No change to a "Must not" item, to the contract, or to behaviour. If a simplification would need a test to change, leave it out and note it in your report. No GitHub write: the pull request is already open and you do not comment on it.

Ownership: production files this PR already changed.

Observable acceptance: the PR's head commit is the worktree's HEAD and `git status` is clean. Send worker_done with --outcome succeeded and a body that lists each simplification in one line with its file, or says "nothing to simplify", and gives the test command with its last line.
