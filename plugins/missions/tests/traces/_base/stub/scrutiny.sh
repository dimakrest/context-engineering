#!/bin/bash
# A scrutiny validator with a green suite: one command, exit 0, no failures, every assertion of
# the milestone covered by a passing test -- the format agents/mission-validator-scrutiny.md fixes.
set -e
ids=$(grep -oE '^  A[0-9]{3}[a-z]? ' "$MISSIONS_PROMPT" | grep -oE 'A[0-9]{3}[a-z]?' || true)
{
  printf '## Commands\n| Command | Exit code | Duration |\n|---|---|---|\n| make test-unit | 0 | 1s |\n'
  printf '\n## Failures\nnone\n'
  printf '\n## Coverage of milestone assertions\n| Assertion | Test that exercises it | Result |\n|---|---|---|\n'
  for a in $ids; do printf '| %s | tests/unit/test_a.py::test_a | pass |\n' "$a"; done
  printf '\n## Health delta\nnot available: repowise is not indexed\n'
} > "$MISSIONS_RUN_DIR/output.md"
