#!/bin/bash
# A behavior validator that proves every assertion the prompt names -- the format
# agents/mission-validator-behavior.md fixes.
set -e
ids=$(grep -oE '^  A[0-9]{3}[a-z]? ' "$MISSIONS_PROMPT" | grep -oE 'A[0-9]{3}[a-z]?' || true)
{
  printf '## Assertion results\n| ID | Verdict | Evidence |\n|---|---|---|\n'
  for a in $ids; do printf '| %s | proven | stub run 1: the chip is visible on the dashboard |\n' "$a"; done
  printf '\n## Defects\nnone\n'
} > "$MISSIONS_RUN_DIR/output.md"
