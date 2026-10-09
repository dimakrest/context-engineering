Target: the directory {OUT} (a scratch directory outside any repository).
Change: this task fails on purpose unless a note is attached. If this spec ends with a paragraph that starts with "Note from the coordinator", do what that note says. If there is no such paragraph, change nothing and report failure.
Constraints: change no file in the worktree; run no tests; run no git command; no network.
Ownership: only {OUT}/flaky.txt, and only when the note asks for it.
Observable acceptance: without a note, send worker_done with --outcome failed and the body "no note was attached". With a note, send worker_done with --outcome succeeded after doing what it says.
