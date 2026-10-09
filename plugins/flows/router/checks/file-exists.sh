#!/usr/bin/env bash
# file-exists.sh <path> [<min lines>]
#
# Prints one line and exits 0 (OK) or 1 (NOT OK). OK when the file exists and has at least <min lines> lines
# (default 1). Why: a worker_done that names a report is not accepted until the report is there.
set -uo pipefail
[ "${1:-}" = "--help" ] && { sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
f="${1:-}"; min="${2:-1}"
[ -n "$f" ] || { echo "usage: file-exists.sh <path> [<min lines>]" >&2; exit 2; }
[ -f "$f" ] || { echo "NOT OK no file $f"; exit 1; }
n="$(grep -c '' "$f")"   # counts a last line that has no newline, which wc -l does not
if [ "$n" -ge "$min" ]; then echo "OK $(basename "$f"): $n lines"; else echo "NOT OK $(basename "$f"): $n lines, expected at least $min"; exit 1; fi
