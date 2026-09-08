#!/bin/bash
# The behavior validator ran the interface assertion and it did not hold: A003 FAILED. Every
# other assertion it is given is proven.
set -e
. "$(dirname "$0")/lib.sh"   # write_behavior
verdict() {
  if [ "$1" = A003 ]; then printf 'FAILED | stub run 1: the dashboard renders no chip; the filter panel is empty'
  else printf 'proven | stub run 1: the chip is visible on the dashboard'; fi
}
write_behavior verdict 'Loaded the dashboard as tenant A with the default window; the filter panel
rendered empty and no chip appeared. Reproduced on stub runs 1-3; trace id stub-M2-A003.' \
  > "$MISSIONS_RUN_DIR/output.md"
