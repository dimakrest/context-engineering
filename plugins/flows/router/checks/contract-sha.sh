#!/usr/bin/env bash
# contract-sha.sh record <file>            prints "VAR CONTRACT_SHA=<sha256>" (a script step stores it) and an OK line
# contract-sha.sh check  <file> <sha256>   OK when the file still has that sha256
#
# Prints one verdict line and exits 0 (OK) or 1 (NOT OK). Why: the accepted contract's sha256 goes into every
# brief, and a contract that changed without an amendment must stop the chain.
set -uo pipefail
[ "${1:-}" = "--help" ] && { sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
mode="${1:-}"; f="${2:-}"; want="${3:-}"
case "$mode" in record|check) : ;; *) echo "usage: contract-sha.sh record <file> | check <file> <sha256>" >&2; exit 2 ;; esac
[ -f "$f" ] || { echo "NOT OK contract: no file $f"; exit 1; }
sha="$(shasum -a 256 "$f" | cut -d' ' -f1)"
if [ "$mode" = record ]; then echo "VAR CONTRACT_SHA=$sha"; echo "OK contract $(basename "$f"): sha256 ${sha:0:12}, $(grep -c '' "$f") lines"; exit 0; fi
[ -n "$want" ] || { echo "usage: contract-sha.sh check <file> <sha256>" >&2; exit 2; }
if [ "$sha" = "$want" ]; then echo "OK contract $(basename "$f"): sha256 ${sha:0:12} as accepted"; else echo "NOT OK contract $(basename "$f"): sha256 ${sha:0:12}, accepted ${want:0:12}"; exit 1; fi
