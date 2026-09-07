#!/bin/bash
# Mutation tests for the missions driver (#6): every case breaks one rule in the driver on a
# throwaway copy of the plugin and asserts that the trace which exists to defend that rule now
# FAILS. A trace suite that stays green under a mutant is not testing what it claims to test.
#
# usage: bash tests/mutants.sh [<mutant-glob>]      e.g. bash tests/mutants.sh 'skip-*'
#
# Each mutant asserts in BOTH directions:
#   breaks=<trace>    must fail under the mutant   -- the rule is defended
#   control=<trace>   must still pass under it     -- the mutation is surgical, not a broken driver
# The second half is what keeps a mutant honest: deleting a random line also turns a trace red.
#
# Case layout: tests/mutants/<name>/
#   mutant     category= file= breaks= control=   (plus # comment lines saying what the rule is)
#   old.txt    the exact source text to replace; must occur EXACTLY ONCE in <file>
#   new.txt    what replaces it
# One trailing newline is stripped from old.txt/new.txt (the editor's, not the anchor's), so an
# anchor is written as the lines it matches. An anchor that no longer matches is reported as
# `anchor not found` rather than a passing mutant -- the suite fails loudly when the driver moves
# under it, which is the only way these keep their value.
#
# Categories: the five #6 names -- continuation, identity, approval, freshness, enforcement --
# plus `evidence`, what counts as proof of an assertion, which VALIDATE added after the ticket
# was written and which no trace defended until now.

set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
plugin=$(cd "$here/.." && pwd)
pattern="${1:-*}"
out_root="$here/mutants/.out"
pass=0; fail=0; failed=()

apply() {  # <src-file> <old.txt> <new.txt>; non-zero with a reason on stderr when it cannot apply
  python3 - "$@" <<'PY'
import io, sys
src, old_p, new_p = sys.argv[1:4]
def read(p):
    t = io.open(p, encoding="utf-8").read()
    return t[:-1] if t.endswith("\n") else t
text = io.open(src, encoding="utf-8").read()
old, new = read(old_p), read(new_p)
n = text.count(old)
if n != 1:
    sys.stderr.write("anchor not found\n" if n == 0 else "anchor matches %d times\n" % n)
    sys.exit(1)
io.open(src, "w", encoding="utf-8").write(text.replace(old, new))
PY
}

run_mutant() {
  local case_dir="$1" name tmp mod category="" file="" breaks="" control="" line ok=1
  name=$(basename "$case_dir")
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      category=*) category="${line#category=}" ;;
      file=*)     file="${line#file=}" ;;
      breaks=*)   breaks="${line#breaks=}" ;;
      control=*)  control="${line#control=}" ;;
      ''|'#'*)    ;;
      *)          echo "FAIL $name: bad mutant line: $line"; return 1 ;;
    esac
  done < "$case_dir/mutant"
  for k in category file breaks control; do
    [ -n "${!k}" ] || { echo "FAIL $name: $k= is missing from mutant"; return 1; }
  done

  tmp=$(mktemp -d)
  # exclude rather than copy-then-delete: keep() leaves a whole plugin copy under .out on every
  # failure, so copying it would grow with each red mutant
  mkdir -p "$tmp/plugin"
  if ! tar -C "$plugin" --exclude=./tests/traces/.out --exclude=./tests/mutants/.out \
          --exclude=__pycache__ -cf - . | tar -C "$tmp/plugin" -xf -; then
    echo "FAIL $name [$category]: could not copy the plugin"; rm -rf "$tmp"; return 1
  fi
  if [ ! -f "$tmp/plugin/$file" ]; then
    echo "FAIL $name [$category]: $file does not exist"; rm -rf "$tmp"; return 1
  fi
  if ! apply "$tmp/plugin/$file" "$case_dir/old.txt" "$case_dir/new.txt" 2>"$tmp/.apply.err"; then
    echo "FAIL $name [$category]: cannot mutate $file -- $(cat "$tmp/.apply.err")"
    echo "      the driver moved under this mutant; re-anchor tests/mutants/$name/old.txt"
    rm -rf "$tmp"; return 1
  fi
  # the mutated file must still be importable: a mutant is a behaviour change, not a syntax error.
  # The dotted name comes from the path, so a mutant on an adapter (missions.adapters.codex) is
  # checked too -- `import missions.loop` alone reaches 14 of the 15 modules but no concrete adapter.
  mod="${file#driver/}"; mod="${mod%.py}"; mod="${mod//\//.}"
  if ! (cd "$tmp/plugin/driver" && python3 -c "import $mod") >"$tmp/.import.log" 2>&1; then
    echo "FAIL $name [$category]: the mutated driver does not import:"
    sed 's/^/      /' "$tmp/.import.log" | tail -5; keep "$name" "$tmp"; return 1
  fi

  local -a why=()
  if bash "$tmp/plugin/tests/traces/run.sh" "$breaks" >"$tmp/.breaks.log" 2>&1; then
    ok=0; why+=("$breaks still PASSES under this mutant -- the trace does not defend $category")
  fi
  if ! bash "$tmp/plugin/tests/traces/run.sh" "$control" >"$tmp/.control.log" 2>&1; then
    ok=0; why+=("$control also fails -- the mutation is not surgical, so breaking $breaks proves nothing")
  fi
  if [ "$ok" = 1 ]; then
    echo "ok   $name [$category]  broke $breaks, kept $control"; rm -rf "$tmp"; return 0
  fi
  echo "FAIL $name [$category]"
  for w in "${why[@]}"; do echo "      $w"; done
  tail -4 "$tmp/.breaks.log" | sed 's/^/      breaks: /'
  tail -4 "$tmp/.control.log" | sed 's/^/      control: /'
  keep "$name" "$tmp"
  return 1
}

keep() { mkdir -p "$out_root"; rm -rf "$out_root/$1"; mv "$2" "$out_root/$1"; echo "      kept: $out_root/$1"; }

start=$(date +%s)
rm -rf "$out_root"
for c in "$here"/mutants/$pattern/; do
  [ -f "$c/mutant" ] || continue
  if run_mutant "${c%/}"; then pass=$((pass + 1)); else fail=$((fail + 1)); failed+=("$(basename "$c")"); fi
done
echo
echo "passed $pass · failed $fail · $(( $(date +%s) - start ))s"
for f in "${failed[@]:-}"; do [ -n "$f" ] && echo "  - $f"; done
[ "$fail" = 0 ] && [ "$pass" -gt 0 ]
