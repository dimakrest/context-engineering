#!/bin/bash
# A reviewer for whom A002 is never satisfied, whatever was repaired.
set -e
ids=$(grep -oE '^  A[0-9]{3}[a-z]? ' "$MISSIONS_PROMPT" | grep -oE 'A[0-9]{3}[a-z]?' || true)
{
  printf '## Assertion verdicts\n| ID | Verdict | Evidence / breaking case |\n|---|---|---|\n'
  for a in $ids; do
    if [ "$a" = A002 ]; then printf '| A002 | not satisfied | tenant B rows still come back for tenant A |\n'
    else printf '| %s | satisfied | stub |\n' "$a"; fi
  done
  printf '\n## Design conformance\n| D-id | Verdict (conforms / deviates / cannot tell) | Evidence |\n|---|---|---|\n| D001 | conforms | stub |\n'
  printf '\n## Impact\n| Changed symbol | Caller (file:line) | Behaviour change | Covered by | Verdict |\n|---|---|---|---|---|\n'
  printf '\n## Defects\n| Severity | file:line | What breaks, and the concrete input that breaks it |\n|---|---|---|\n| high | analytics/service.py:3 | no tenant filter |\n'
  printf '\n## Not covered by any assertion\nnone\n'
} > "$MISSIONS_RUN_DIR/output.md"
