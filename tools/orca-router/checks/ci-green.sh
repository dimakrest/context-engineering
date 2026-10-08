#!/usr/bin/env bash
# ci-green.sh [--watch] <pr number or url> [-R <owner/repo>]
#
# Prints one line and exits 0 (OK) or 1 (NOT OK). OK when no check of the PR failed, was cancelled or is pending.
# --watch polls until nothing is pending (every CI_POLL_S seconds, default 30); bound it from outside (the
# router's script step has a timeout). Read-only: it only runs `gh pr checks`.
# Why: the coordinator reads this line before a merge, not the checks table.
set -uo pipefail
[ "${1:-}" = "--help" ] && { sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
watch=""; [ "${1:-}" = "--watch" ] && { watch=1; shift; }
[ $# -ge 1 ] || { echo "usage: ci-green.sh [--watch] <pr number or url> [-R <owner/repo>]" >&2; exit 2; }
quiet=0
while :; do
  json="$(gh pr checks "$@" --json name,bucket 2>/dev/null)"
  line="$(printf '%s' "$json" | python3 -c '
import json, sys, collections
try:
    rows = json.load(sys.stdin)
except ValueError:
    print("EMPTY"); sys.exit()
if not rows:
    print("EMPTY"); sys.exit()
c = collections.Counter(r.get("bucket") for r in rows)
bad = [r["name"] for r in rows if r.get("bucket") in ("fail", "cancel")]
counts = ", ".join(f"{c[k]} {k}" for k in ("fail", "cancel", "pending", "pass", "skipping") if c[k])
if bad:
    print("NOT OK ci: " + counts + " (" + ", ".join(bad[:3]) + ")")
elif c["pending"]:
    print("PENDING ci: " + counts)
else:
    print("OK ci: " + counts)
')"
  case "$line" in
    OK*)      echo "$line"; exit 0 ;;
    "NOT OK"*) echo "$line"; exit 1 ;;
    EMPTY)    quiet=$((quiet+1))   # no check has been reported yet (CI has not started), or gh failed
              if [ -z "$watch" ] || [ "$quiet" -ge 10 ]; then echo "NOT OK ci: gh reported no checks for $1"; exit 1; fi ;;
    *)        [ -n "$watch" ] || { echo "NOT OK ${line#PENDING }"; exit 1; } ;;
  esac
  sleep "${CI_POLL_S:-30}"
done
