#!/bin/bash
# Trace tests for the missions driver: every case runs the REAL driver over a temporary git repo
# with the stub adapter (a shell script plays the worker) and asserts on the journal, git, the
# state block, files, and the driver's exit code. The hook suite (tests/run.sh) checks refusal;
# a green trace proves progress.
#
# usage: tests/traces/run.sh [<case-glob>]        e.g. tests/traces/run.sh 'two-feature*'
#
# Case layout: tests/traces/<name>/ overlays tests/traces/_base/:
#   mission/*         copied over _base/mission/  -> <tmp>/repo/.missions/demo/
#   stub/*.sh         copied over _base/stub/     -> the stub script dir (<role>.sh, or <feature>.sh)
#   repo.sh           replaces _base/repo.sh (builds <tmp>/repo; argv 1 = <tmp>)
#   expect            required. Lines:
#     rc=<n>              expected exit code of `missions run` (default 0)
#     args=<argv>         extra arguments to `missions run` (word-split)
#     env=KEY=VALUE       extra environment for the driver (repeatable)
#     journal~=<regex>    journal.jsonl must match, IN ORDER across lines (repeatable)
#     journal!~=<regex>   journal.jsonl must not match (repeatable)
#     git~=<regex>        `git log --oneline` of the repo must match (repeatable)
#     state~=<regex>      the ```mission-state block of state.md must match (repeatable)
#     stdout~=<regex>     the driver's stdout must match (repeatable)
#     file=<path>         must exist, relative to the mission dir (repeatable)
#     nofile=<path>       must not exist, relative to the mission dir (repeatable)
#     postcheck=<shell>   run in <tmp>/repo with $M (mission dir), $TMP, $plugin set; non-zero = fail
#   mission/driver.json optional; when present `@STUB@` is replaced by the stub dir, else `missions init`
#
# A failed case keeps its tmp dir under tests/traces/.out/<name>/ for inspection.

set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
plugin=$(cd "$here/../.." && pwd)
pattern="${1:-*}"
out_root="$here/.out"
pass=0; fail=0; failed=()

drv() {  # run the driver with a clean environment
  local tmp="$1"; shift
  env -i HOME="$HOME" PATH="$PATH" TMPDIR="$tmp" LC_ALL=C.UTF-8 MISSIONS_PLUGIN_ROOT="$plugin" "$@"
}

run_case() {
  local case_dir="$1" name tmp m stubs repo_sh rc_exp=0 args="" rc ok=1 line out
  local -a envs=() j_re=() j_not=() g_re=() s_re=() o_re=() files=() nofiles=() posts=()
  name=$(basename "$case_dir")
  tmp=$(mktemp -d)
  repo_sh="$here/_base/repo.sh"; [ -f "$case_dir/repo.sh" ] && repo_sh="$case_dir/repo.sh"
  if ! bash "$repo_sh" "$tmp" >"$tmp/.repo.log" 2>&1; then
    echo "FAIL $name: repo.sh failed:"; sed 's/^/      /' "$tmp/.repo.log"; keep "$name" "$tmp"; return 1
  fi
  m="$tmp/repo/.missions/demo"; mkdir -p "$m"
  cp -R "$here/_base/mission/." "$m/"
  [ -d "$case_dir/mission" ] && cp -R "$case_dir/mission/." "$m/"
  stubs="$tmp/stub"; mkdir -p "$stubs"; cp -R "$here/_base/stub/." "$stubs/"
  [ -d "$case_dir/stub" ] && cp -R "$case_dir/stub/." "$stubs/"
  if [ -f "$m/driver.json" ]; then
    sed -i "s|@STUB@|$stubs|g" "$m/driver.json"
  elif ! drv "$tmp" bash "$plugin/bin/missions" init "$m" --harness stub --stub-dir "$stubs" >"$tmp/.init.log" 2>&1; then
    echo "FAIL $name: missions init failed:"; sed 's/^/      /' "$tmp/.init.log"; keep "$name" "$tmp"; return 1
  fi
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      rc=*)         rc_exp="${line#rc=}" ;;
      args=*)       args="${line#args=}" ;;
      env=*)        envs+=("${line#env=}") ;;
      'journal!~='*) j_not+=("${line#journal!~=}") ;;
      'journal~='*) j_re+=("${line#journal~=}") ;;
      'git~='*)     g_re+=("${line#git~=}") ;;
      'state~='*)   s_re+=("${line#state~=}") ;;
      'stdout~='*)  o_re+=("${line#stdout~=}") ;;
      file=*)       files+=("${line#file=}") ;;
      nofile=*)     nofiles+=("${line#nofile=}") ;;
      postcheck=*)  posts+=("${line#postcheck=}") ;;
      ''|'#'*)      ;;
      *)            echo "FAIL $name: bad expect line: $line"; keep "$name" "$tmp"; return 1 ;;
    esac
  done < "$case_dir/expect"
  local -a argv=(); eval "argv=($args)"
  out=$(cd "$tmp/repo" && drv "$tmp" env "${envs[@]:-MISSIONS_TEST=1}" bash "$plugin/bin/missions" run "$m" ${argv[@]+"${argv[@]}"} 2>"$tmp/.stderr"); rc=$?
  printf '%s\n' "$out" > "$tmp/.stdout"
  local -a why=()
  [ "$rc" = "$rc_exp" ] || { ok=0; why+=("rc=$rc, expected $rc_exp"); }
  local pos=0 hit
  for re in "${j_re[@]:-}"; do
    [ -n "$re" ] || continue
    hit=$(tail -n +$((pos + 1)) "$m/journal.jsonl" 2>/dev/null | grep -n -E -m1 -- "$re" | cut -d: -f1)
    if [ -z "$hit" ]; then ok=0; why+=("journal~=$re  (not found after line $pos)"); else pos=$((pos + hit)); fi
  done
  for re in "${j_not[@]:-}"; do
    [ -n "$re" ] || continue
    grep -qE -- "$re" "$m/journal.jsonl" 2>/dev/null && { ok=0; why+=("journal!~=$re  (found)"); }
  done
  for re in "${g_re[@]:-}"; do
    [ -n "$re" ] || continue
    # not grep -q: under pipefail a producer killed by SIGPIPE fails the pipeline even when grep matched
    git -C "$tmp/repo" log --oneline | grep -E -- "$re" >/dev/null || { ok=0; why+=("git~=$re"); }
  done
  for re in "${s_re[@]:-}"; do
    [ -n "$re" ] || continue
    awk '/^```mission-state[[:space:]]*$/{on=1;next} on&&/^```/{exit} on{print}' "$m/state.md" | grep -E -- "$re" >/dev/null \
      || { ok=0; why+=("state~=$re"); }
  done
  for re in "${o_re[@]:-}"; do
    [ -n "$re" ] || continue
    grep -qE -- "$re" "$tmp/.stdout" || { ok=0; why+=("stdout~=$re"); }
  done
  for f in "${files[@]:-}"; do [ -z "$f" ] || [ -e "$m/$f" ] || { ok=0; why+=("file=$f missing"); }; done
  for f in "${nofiles[@]:-}"; do [ -z "$f" ] || [ ! -e "$m/$f" ] || { ok=0; why+=("nofile=$f exists"); }; done
  for pc in "${posts[@]:-}"; do
    [ -n "$pc" ] || continue
    (cd "$tmp/repo" && M="$m" TMP="$tmp" plugin="$plugin" eval "$pc") >/dev/null 2>&1 || { ok=0; why+=("postcheck=$pc"); }
  done
  if [ "$ok" = 1 ]; then
    echo "ok   $name"; rm -rf "$tmp"; return 0
  fi
  echo "FAIL $name"
  for w in "${why[@]}"; do echo "      $w"; done
  echo "      stderr: $(tail -3 "$tmp/.stderr" | tr '\n' ' ' | cut -c1-300)"
  echo "      stdout: $(tail -3 "$tmp/.stdout" | tr '\n' ' ' | cut -c1-300)"
  keep "$name" "$tmp"
  return 1
}

keep() { mkdir -p "$out_root"; rm -rf "$out_root/$1"; mv "$2" "$out_root/$1"; echo "      kept: $out_root/$1"; }

start=$(date +%s)
rm -rf "$out_root"
if [ "$pattern" = "*" ]; then
  if python3 "$here/../driver-selftest.py" >"$here/.selftest.log" 2>&1; then
    echo "ok   driver-selftest"; pass=$((pass + 1))
  else
    echo "FAIL driver-selftest"; sed 's/^/      /' "$here/.selftest.log" | tail -20; fail=$((fail + 1)); failed+=("driver-selftest")
  fi
  rm -f "$here/.selftest.log"
fi
for c in "$here"/$pattern/; do
  [ -f "$c/expect" ] || continue
  if run_case "${c%/}"; then pass=$((pass + 1)); else fail=$((fail + 1)); failed+=("$(basename "$c")"); fi
done
echo
echo "passed $pass · failed $fail · $(( $(date +%s) - start ))s"
for f in "${failed[@]:-}"; do [ -n "$f" ] && echo "  - $f"; done
[ "$fail" = 0 ]
