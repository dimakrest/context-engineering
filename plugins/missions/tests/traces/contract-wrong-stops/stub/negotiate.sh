#!/bin/bash
# Negotiate finds the contract itself wrong: a BLOCK halt for the user, never a rewritten assertion.
set -e
printf '{"findings":[],"repairs":[],"contract_wrong":true,"reason":"A002 as written cannot hold: tenants share the aggregate by design, and the fix would change user-visible scope"}\n' > "$MISSIONS_RUN_DIR/output.md"
