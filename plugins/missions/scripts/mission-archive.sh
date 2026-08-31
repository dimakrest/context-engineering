#!/bin/bash
# Move a closed milestone's sections out of state.md into archive/M<n>.md.
#
# usage: mission-archive.sh <mission-dir> M<n>
#
# state.md is the hot file: every dispatched agent is briefed from it. Closed
# milestone history belongs in the cold file. Sections are `## M<n>...` headings
# (e.g. "## M1 CLOSED 2026-08-30", "## M1b — ...") up to the next `## ` heading
# that does not itself start with M<n>. A one-line pointer is left behind.
# Exit 0 = moved (or nothing to move). Exit 1 = bad arguments.

set -uo pipefail
m="${1:-}"; ms="${2:-}"
[ -d "$m" ] && [ -f "$m/state.md" ] && [[ "$ms" =~ ^M[0-9]+$ ]] || {
  echo "usage: mission-archive.sh <mission-dir> M<n>" >&2; exit 1; }

mkdir -p "$m/archive"
arch="$m/archive/$ms.md"
tmp=$(mktemp)
moved=$(awk -v ms="$ms" -v arch="$arch" '
  BEGIN { moved = 0 }
  /^##[[:space:]]/ {
    if ($0 ~ "^##[[:space:]]+" ms "([^0-9]|$)") { on = 1; moved++; print >> arch; print "" >> arch; next }
    else if (on) { on = 0 }
  }
  on { print >> arch; next }
  { print }
  END { print moved }
' "$m/state.md" > "$tmp")
moved=$(tail -1 "$tmp"); sed '$d' "$tmp" > "$m/state.md.new"; rm -f "$tmp"

if [ "${moved:-0}" -eq 0 ]; then rm -f "$m/state.md.new"; echo "mission-archive: no '## $ms' sections in state.md" >&2; exit 0; fi
printf '\n## %s — archived\nClosed history moved to `archive/%s.md` (%s sections). Ids stay addressable there.\n' "$ms" "$ms" "$moved" >> "$m/state.md.new"
mv "$m/state.md.new" "$m/state.md"
echo "archived $moved section(s) of $ms to $arch; state.md is now $(wc -l < "$m/state.md" | tr -d ' ') lines"
exit 0
