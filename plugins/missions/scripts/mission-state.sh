#!/bin/bash
# The mission digest: everything an agent needs from state.md, in under 2 KB.
#
# usage: mission-state.sh <mission-dir>
#
# Replaces "read state.md first". The last mission broadcast a 22 KB state file
# into 101 dispatch briefings (~3.1 M tokens, a third of it closed history) while
# the orchestrator itself survived on 558-byte id-scoped reads. This prints:
#   - the fenced ```mission-state block (or the legacy header, with a warning)
#   - the hook-owned locks (.writer, .lease)
#   - ## Open issues
#   - ## Standing constraints for every agent   (verbatim -- rules are never truncated)
#   - resume_next
# Exit 0 = digest printed. Exit 2 = it cannot fit in 2048 bytes; the standing
# constraints must be shortened or moved to id-addressable files, because a
# silently truncated rule is worse than a loud failure.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../hooks/mission-lib.sh"

m="${1:-}"
[ -n "$m" ] && [ -f "$m/state.md" ] || { echo "usage: mission-state.sh <mission-dir>" >&2; exit 1; }
CAP=2048

section() { # prints a ## section body verbatim (until the next ## heading)
  awk -v h="$1" '
    $0 ~ "^##[[:space:]]+" h { on = 1; print; next }
    on && /^##[[:space:]]/   { exit }
    on { print }
  ' "$m/state.md"
}

out=""
if [ "$(mission_has_block "$m")" = 1 ]; then
  out+=$(awk '/^```mission-state[[:space:]]*$/{on=1} on{print} on && NR>1 && /^```[[:space:]]*$/ && !/mission-state/{exit}' "$m/state.md")
else
  echo "warning: legacy state header (no \`\`\`mission-state block) -- parsed from **Phase:** lines" >&2
  out+="phase: $(mission_phase "$m")"$'\n'
  out+="milestone: $(mission_legacy_field "$m" Milestone | cut -c1-120)"$'\n'
  out+="spend: $(mission_legacy_field "$m" Spend | cut -c1-120)"$'\n'
  out+="resume_next: <unset>"$'\n'
fi
out+=$'\n'
out+="writer: $( { w=$(mission_active_writer "$m"); [ -n "$w" ] && printf '%s' "$w" || printf 'none'; } )"$'\n'
lf=$(mission_lease_file "$m")
out+="lease: $( [ -f "$lf" ] && cat "$lf" || printf 'free' )"$'\n'
[ -n "$(mission_field "$m" resume_next)" ] || [ "$(mission_has_block "$m")" = 0 ] || out+="resume_next: <unset>"$'\n'
out+=$'\n'
oi=$(section '[Oo]pen issues'); [ -n "$oi" ] && out+="$oi"$'\n'$'\n'
sc=$(section '[Ss]tanding constraints'); [ -n "$sc" ] && out+="$sc"$'\n'

size=${#out}
if [ "$size" -gt "$CAP" ]; then
  echo "digest cannot fit: $size bytes > $CAP. Shorten '## Standing constraints for every agent' in $m/state.md (move project rules into the repo's own CLAUDE.md / rules files and reference them by path)." >&2
  printf '%s\n' "$out" | head -c "$CAP" >&2
  exit 2
fi
printf '%s' "$out"
exit 0
