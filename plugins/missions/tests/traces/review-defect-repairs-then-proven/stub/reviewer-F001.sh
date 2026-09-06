#!/bin/bash
# F001's reviewer: on the first review A002 is not satisfied, with the concrete breaking input;
# on the repair round every assertion is satisfied. F002 and the repair use the base reviewer.
set -e
if ls "$MISSIONS_DIR/handoffs" >/dev/null 2>&1; then echo present; else echo absent; fi > "$MISSIONS_RUN_DIR/handoffs-visible.txt"
ids=$(grep -oE '^  A[0-9]{3}[a-z]? ' "$MISSIONS_PROMPT" | grep -oE 'A[0-9]{3}[a-z]?' || true)
first=0; [ "$MISSIONS_TASK" = "review-F001#1" ] && first=1
{
  printf '## Assertion verdicts\n| ID | Verdict | Evidence / breaking case |\n|---|---|---|\n'
  for a in $ids; do
    if [ "$a" = A002 ] && [ "$first" = 1 ]; then
      printf '| A002 | not satisfied | tenant A with rows of tenant B: `analytics/service.py:3` has no tenant filter |\n'
    else
      printf '| %s | satisfied | stub: `analytics/service.py:1` |\n' "$a"
    fi
  done
  printf '\n## Design conformance\n| D-id | Verdict (conforms / deviates / cannot tell) | Evidence |\n|---|---|---|\n| D001 | conforms | stub |\n'
  printf '\n## Impact\n| Changed symbol | Caller (file:line) | Behaviour change | Covered by | Verdict |\n|---|---|---|---|---|\n'
  printf '\n## Defects\n| Severity | file:line | What breaks, and the concrete input that breaks it |\n|---|---|---|\n'
  [ "$first" = 1 ] && printf '| high | analytics/service.py:3 | no tenant filter; input: tenant A, rows of B |\n'
  printf '\n## Not covered by any assertion\nnone\n'
} > "$MISSIONS_RUN_DIR/output.md"
