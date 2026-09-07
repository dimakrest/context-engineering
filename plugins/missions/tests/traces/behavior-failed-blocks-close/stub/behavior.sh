#!/bin/bash
# The behavior validator ran the interface assertion and it did not hold: A003 FAILED. Every
# other assertion it is given is proven.
set -e
. "$(dirname "$0")/lib.sh"   # write_behavior
verdict() {
  if [ "$1" = A003 ]; then printf 'FAILED | stub run 1: the dashboard renders no chip; the filter panel is empty'
  else printf 'proven | stub run 1: the chip is visible on the dashboard'; fi
}
write_behavior verdict '| high | ui/src/Filters.tsx:1 | the chip never renders |' > "$MISSIONS_RUN_DIR/output.md"
