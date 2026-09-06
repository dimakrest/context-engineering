#!/bin/bash
# A reviewer that finds nothing: every assertion the prompt names is `satisfied`, in the format
# agents/mission-reviewer.md fixes. It also records whether the handoffs were visible to it --
# the reviewer-cannot-see-handoffs trace asserts `absent`.
set -e
if ls "$MISSIONS_DIR/handoffs" >/dev/null 2>&1; then echo present; else echo absent; fi > "$MISSIONS_RUN_DIR/handoffs-visible.txt"
ids=$(grep -oE '^  A[0-9]{3}[a-z]? ' "$MISSIONS_PROMPT" | grep -oE 'A[0-9]{3}[a-z]?' || true)
{
  printf '## Assertion verdicts\n| ID | Verdict | Evidence / breaking case |\n|---|---|---|\n'
  for a in $ids; do printf '| %s | satisfied | stub: `analytics/service.py:1` |\n' "$a"; done
  printf '\n## Design conformance\n| D-id | Verdict (conforms / deviates / cannot tell) | Evidence |\n|---|---|---|\n| D001 | conforms | stub |\n'
  printf '\n## Impact\n| Changed symbol | Caller (file:line) | Behaviour change | Covered by | Verdict |\n|---|---|---|---|---|\n'
  printf '\n## Defects\n| Severity | file:line | What breaks, and the concrete input that breaks it |\n|---|---|---|\n'
  printf '\n## Not covered by any assertion\nnone\n'
} > "$MISSIONS_RUN_DIR/output.md"
