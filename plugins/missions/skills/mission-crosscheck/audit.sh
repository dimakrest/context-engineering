#!/bin/bash
# Contamination gate for /missions:mission-crosscheck. Run BEFORE reading a single finding.
#
# usage: audit.sh <transcript> <mission-dir> <pre-state-snapshot-dir>
#
# A contaminated review is indistinguishable from a clean one by reading it:
# fluent, well cited, and quietly echoing the answer it was supposed to derive
# independently. The only thing that separates them is what the reviewer opened.
# This script is that check, and it is a gate, not a courtesy.
#
# Exit 0 = the run is trustworthy. Exit 1 = void it; do not quote it.

set -uo pipefail

transcript="${1:-}"; mission="${2:-}"; snap="${3:-}"
[ -f "$transcript" ] || { echo "AUDIT FAIL: no transcript at '$transcript'"; exit 1; }

fail=0
say() { echo "$1"; }

# --- 1. Did the reviewer OPEN anything it was sealed away from? ---------------
#
# Discriminating a read from a mention is the whole difficulty. Three things
# legitimately name these paths: our own task file, the reviewer stating its
# plan, and its own search commands carrying the exclusions. What must never
# appear is a read verb pointed AT them.
#
# Read verbs, then a forbidden root on the same line, minus lines where the path
# is being excluded (rg/grep glob negation) rather than opened.
V="(nl|cat|sed|head|tail|less|more|wc|awk|open|xxd|strings|python3?)"
X="!\.?(missions|docs)|--glob[[:space:]]*'?!|[-]g[[:space:]]*'?!|--exclude"
reads=$(grep -nE "(^|[^[:alnum:]_-])${V}[[:space:]][^|]{0,200}(\.missions/|docs/plans/)" "$transcript" \
        | grep -vE "$X" || true)

# Discovery counts too: enumerating the sealed directory is how run 1 found the
# originals after its own exclusion hid the package from it.
discovery=$(grep -nE "(^|[^[:alnum:]_-])(find|ls|tree|rg|grep)[[:space:]][^|]{0,200}\.missions" "$transcript" \
            | grep -vE "$X" || true)

# Citations are a weaker but independent signal. With the package staged outside
# the repo, nothing the reviewer legitimately saw lives under .missions/ -- so a
# [verified: .missions/...] tag means it read the original, whatever its command
# log shows.
cites=$(grep -nE "verified:[^]]*\.missions/" "$transcript" || true)

if [ -n "$reads" ] || [ -n "$discovery" ] || [ -n "$cites" ]; then
  say "AUDIT FAIL: the reviewer reached sealed material."
  say ""
  [ -n "$reads" ]     && { say "  reads:";     printf '%s\n' "$reads"     | head -4 | cut -c1-160 | sed 's/^/    /'; }
  [ -n "$discovery" ] && { say "  discovery:"; printf '%s\n' "$discovery" | head -4 | sed 's/^/    /'; }
  [ -n "$cites" ]     && { say "  citations to sealed paths:"; printf '%s\n' "$cites" | head -4 | cut -c1-160 | sed 's/^/    /'; }
  say ""
  say "  This run is VOID. Quarantine it as VOID-*, fix the seal, re-run."
  say "  Do not salvage it as a blind pass -- not partially, not for one section."
  fail=1
fi

# --- 2. Did it write anything? Its sandbox defaults to workspace-write. -------
if [ -n "$snap" ] && [ -d "$snap" ]; then
  if [ -f "$snap/git.txt" ]; then
    if ! git status --short 2>/dev/null | diff -q "$snap/git.txt" - >/dev/null 2>&1; then
      say "AUDIT FAIL: git status changed during the run."
      git status --short 2>/dev/null | diff "$snap/git.txt" - | head -10 | sed 's/^/    /'
      fail=1
    fi
  fi
  if [ -f "$snap/mission.sha" ] && [ -n "$mission" ] && [ -d "$mission" ]; then
    # Recompute through snapshot.sh, never with a second copy of the find
    # expression -- the baseline was written by the same code, so the two halves
    # of this check cannot disagree about what counts as a mission file.
    now=$(bash "$(dirname "${BASH_SOURCE[0]}")/snapshot.sh" --print "$mission")
    if ! printf '%s\n' "$now" | diff -q "$snap/mission.sha" - >/dev/null 2>&1; then
      say "AUDIT FAIL: a mission file changed during the run."
      printf '%s\n' "$now" | diff "$snap/mission.sha" - | head -10 | sed 's/^/    /'
      fail=1
    fi
  fi
fi

# --- 3. Is there actually a report to read? ----------------------------------
if ! grep -q "^tokens used" "$transcript"; then
  say "AUDIT FAIL: transcript has no terminal 'tokens used' marker -- the run did"
  say "  not finish. Do not extract a partial report; re-run it."
  fail=1
fi

[ "$fail" -eq 0 ] && say "AUDIT PASS: no sealed material opened, nothing written, report present."
exit "$fail"
