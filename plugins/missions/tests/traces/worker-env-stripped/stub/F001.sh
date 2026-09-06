#!/bin/bash
# Records the environment it was given (names only) and the git config git actually resolves
# under it, then does the base worker's job.
set -e
env | cut -d= -f1 | sort > "$MISSIONS_RUN_DIR/env.txt"
{
  printf 'helper=%s\n' "$(git config credential.helper || true)"
  printf 'hooksPath=%s\n' "$(git config core.hooksPath || true)"
  printf 'email=%s\n' "$(git config user.email || true)"
} > "$MISSIONS_RUN_DIR/git.txt"
exec bash "$(dirname "$MISSIONS_STUB_SCRIPT")/worker.sh"
