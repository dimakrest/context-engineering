#!/bin/bash
# The behavior validator ran the interface assertion and it did not hold: A003 FAILED. Every
# other assertion it is given is proven.
set -e
. "$(dirname "$0")/lib.sh"   # prompt_assertions
{
  printf '## Assertion results\n| ID | Verdict | Evidence |\n|---|---|---|\n'
  for a in $(prompt_assertions); do
    if [ "$a" = A003 ]; then
      printf '| %s | FAILED | stub run 1: the dashboard renders no chip; the filter panel is empty |\n' "$a"
    else
      printf '| %s | proven | stub run 1: the chip is visible on the dashboard |\n' "$a"
    fi
  done
  printf '\n## Defects\n| Severity | Where | What breaks |\n|---|---|---|\n| high | ui/src/Filters.tsx:1 | the chip never renders |\n'
} > "$MISSIONS_RUN_DIR/output.md"
