Target: the directory {OUT} (a scratch directory outside any repository).
Change: ask the coordinator one question with the ask command from your preamble: "Which word goes into ask.txt: PASS or FAIL?" with the options PASS and FAIL. Wait for the answer (if the ask times out, resume the same message id). Then create {OUT}/ask.txt whose only line is: VERDICT: <the answer>
Constraints: change no file in the worktree; run no tests; run no git command; no network. Do not guess the answer.
Ownership: only {OUT}/ask.txt.
Observable acceptance: after the file exists, send worker_done with --outcome succeeded and a one-sentence body naming the answer you received.
