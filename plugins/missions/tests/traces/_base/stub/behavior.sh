#!/bin/bash
# A behavior validator that proves every assertion the prompt names -- the format
# agents/mission-validator-behavior.md fixes.
set -e
. "$(dirname "$0")/lib.sh"   # prompt_assertions
{
  printf '## Assertion results\n| ID | Verdict | Evidence |\n|---|---|---|\n'
  for a in $(prompt_assertions); do printf '| %s | proven | stub run 1: the chip is visible on the dashboard |\n' "$a"; done
  printf '\n## Defects\nnone\n'
} > "$MISSIONS_RUN_DIR/output.md"
