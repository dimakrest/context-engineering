#!/bin/bash
# A reviewer for whom A002 is never satisfied, whatever was repaired.
set -e
. "$(dirname "$0")/lib.sh"   # write_review
verdict() {
  if [ "$1" = A002 ]; then printf 'not satisfied | tenant B rows still come back for tenant A'
  else printf 'satisfied | stub'; fi
}
write_review verdict '| high | analytics/service.py:3 | no tenant filter |' > "$MISSIONS_RUN_DIR/output.md"
