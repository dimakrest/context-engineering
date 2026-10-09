Target: the directory {OUT} (a scratch directory outside any repository).
Change: create the file {OUT}/{STEP}.txt containing the single word OK. Then send one heartbeat exactly as your preamble instructs, with the phase text {STEP}.
Constraints: change no file in the worktree; run no tests; run no git command; no network. Do not read or write any other file under {OUT}.
Ownership: only {OUT}/{STEP}.txt.
Observable acceptance: after the file exists, send worker_done with --outcome succeeded and a one-sentence body.
