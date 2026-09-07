#!/bin/bash
# F003's reviewer: the chip is there (A003 satisfied), but the dashboard query it added leaks
# tenant B's rows to tenant A -- A002, proven in M1, is not satisfied by this patch.
set -e
. "$(dirname "$0")/lib.sh"   # write_review
verdict() {
  if [ "$1" = A002 ]; then printf 'not satisfied | the dashboard query has no tenant predicate: tenant A sees rows of B'
  else printf 'satisfied | stub: `ui/src/Filters.tsx:1`'; fi
}
write_review verdict '| high | ui/src/Filters.tsx:1 | dashboard query without a tenant filter |' > "$MISSIONS_RUN_DIR/output.md"
