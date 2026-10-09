#!/usr/bin/env bash
# verdict-line.sh <file> [<ERE an accepting verdict matches>]
#
# Prints one line and exits 0 (OK) or 1 (NOT OK). OK when the file's first line that starts with "VERDICT:"
# matches the pattern (default: PASS, OK or ACCEPT right after "VERDICT:").
# Why: a worker writes its verdict into its report; the coordinator reads this line, not the report.
set -uo pipefail
[ "${1:-}" = "--help" ] && { sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
f="${1:-}"; pat="${2:-^VERDICT: *(PASS|OK|ACCEPT)}"
[ -n "$f" ] || { echo "usage: verdict-line.sh <file> [<pattern>]" >&2; exit 2; }
[ -f "$f" ] || { echo "NOT OK verdict: no file $f"; exit 1; }
line="$(grep -m1 '^VERDICT:' "$f" | cut -c1-200)"
[ -n "$line" ] || { echo "NOT OK verdict: $(basename "$f") has no line starting with VERDICT:"; exit 1; }
if printf '%s' "$line" | grep -Eq "$pat"; then echo "OK $(basename "$f"): $line"; else echo "NOT OK $(basename "$f"): $line"; exit 1; fi
