Target: inner PR {PR} ("{TITLE}"), triaged. Worktree {WT} at {HEAD_BEFORE}. The contract is {SCRATCH}/contracts/{PR}.md. Rules: before anything else read {SCRATCH}/briefs/rules-worker.md (standing rules and test limits); it binds you.

Change: do every item in {SCRATCH}/reviews/{PR}/fix-tests.md, and nothing else. Write each test from the contract row and the item, not from how the code happens to be written. For an item that names a surviving mutation: write the test, then prove it in a throwaway copy (`git -C {WT} worktree add --detach {SCRATCH}/work/{PR}/fix-tests/wt HEAD`, removed at the end): with the patch applied the test fails on its own assertion, and without it the test passes. Run the tests you added or changed. Commit through the locked-commit script from the rules file, with every hook passing, and push this branch.

Constraints: tests only: no production code, not pytest.ini, not the contract. Do not weaken or delete an existing assertion. If an item cannot be done as written, do the others and say which one and why. No GitHub write.

Ownership: test files under the paths the contract's Scope gives the test writer, {SCRATCH}/reviews/{PR}/fix-tests-done.md and {SCRATCH}/work/{PR}/fix-tests/.

Observable acceptance: the branch is pushed and `git status` is clean. Write {SCRATCH}/reviews/{PR}/fix-tests-done.md: its first line is `VERDICT: OK <n> of <n> items done` or `VERDICT: PARTIAL <k> of <n> items done`, and below it one row per item id with the test node id and, for a survivor, the two last lines (red with the patch, green without). Send worker_done with --outcome succeeded and that first line as the body. If no item could be done, send --outcome failed.
