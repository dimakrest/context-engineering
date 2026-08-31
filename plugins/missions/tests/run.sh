#!/bin/bash
# Regression net for the missions plugin: runs the real hooks and scripts against
# crafted stdin and fixture missions. Inertness cases run first.
#
# usage: tests/run.sh [<case-glob>]        e.g. tests/run.sh 'serial-guard/*'
#
# Case layout: tests/cases/<script>/<case>/
#   expect            required. Lines:
#                       rc=<n>                 expected exit code (default 0)
#                       stderr~=<regex>        stderr must match (repeatable)
#                       stdout~=<regex>        stdout must match (repeatable)
#                       stderr_empty=1         stderr must be empty
#                       script=<rel path>      override the script under test (default: hooks/<script>.sh
#                                              or scripts/<script>.sh, by the parent dir name)
#                       args=<argv>            arguments for scripts (word-split; $TMP expands)
#                       setup=<shell>          run in the tmp dir before the script (repeatable; git fixtures)
#                       postcheck=<shell>      run in the tmp dir after; non-zero = fail (repeatable)
#                       env=KEY=VALUE          extra environment (repeatable)
#   stdin.json        optional; piped to the script ({} when absent)
#   missions/         optional; copied to <tmp>/.missions/
#   *                 every other file is copied into the tmp dir as-is (transcripts, patches…)

set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
plugin=$(cd "$here/.." && pwd)
pattern="${1:-*/*}"
pass=0; fail=0; failed=()

run_case() {
  local case_dir="$1" name script rc_exp=0 rc out err ok=1 line tmp args=""
  name="${case_dir#$here/cases/}"
  script="$plugin/hooks/$(basename "$(dirname "$case_dir")").sh"
  [ -f "$script" ] || script="$plugin/scripts/$(basename "$(dirname "$case_dir")").sh"
  tmp=$(mktemp -d)
  cp -R "$case_dir"/. "$tmp"/ 2>/dev/null
  [ -d "$tmp/missions" ] && mv "$tmp/missions" "$tmp/.missions"
  local -a envs=() setups=() posts=() serr=() sout=(); local stderr_empty=0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      rc=*)           rc_exp="${line#rc=}" ;;
      script=*)       script="$plugin/${line#script=}" ;;
      args=*)         args="${line#args=}" ;;
      setup=*)        setups+=("${line#setup=}") ;;
      postcheck=*)    posts+=("${line#postcheck=}") ;;
      env=*)          envs+=("${line#env=}") ;;
      stderr~=*)      serr+=("${line#stderr~=}") ;;
      stdout~=*)      sout+=("${line#stdout~=}") ;;
      stderr_empty=1) stderr_empty=1 ;;
    esac
  done < "$case_dir/expect"
  args="${args//\$TMP/$tmp}"
  local stdin="$tmp/stdin.json"; [ -f "$stdin" ] || printf '{}' > "$stdin"
  sed -i '' "s#\\\$TMP#$tmp#g" "$stdin" 2>/dev/null || true
  for s in "${setups[@]:-}"; do [ -n "$s" ] && (cd "$tmp" && eval "$s") >/dev/null 2>&1; done
  local -a argv=(); eval "argv=($args)"
  out=$(cd "$tmp" && env -i HOME="$HOME" PATH="$PATH" TMPDIR="$tmp" CLAUDE_PROJECT_DIR="$tmp" CLAUDE_PLUGIN_ROOT="$plugin" "${envs[@]:-MISSIONS_TEST=1}" \
        bash "$script" ${argv[@]+"${argv[@]}"} < "$stdin" 2> "$tmp/.stderr"); rc=$?
  err=$(cat "$tmp/.stderr")
  [ "$rc" = "$rc_exp" ] || { ok=0; echo "    rc: expected $rc_exp, got $rc"; }
  for r in "${serr[@]:-}"; do [ -z "$r" ] || printf '%s' "$err" | grep -qE -- "$r" || { ok=0; echo "    stderr !~ /$r/"; }; done
  for r in "${sout[@]:-}"; do [ -z "$r" ] || printf '%s' "$out" | grep -qE -- "$r" || { ok=0; echo "    stdout !~ /$r/"; }; done
  [ "$stderr_empty" = 1 ] && [ -n "$err" ] && { ok=0; echo "    stderr not empty"; }
  for p in "${posts[@]:-}"; do [ -z "$p" ] || (cd "$tmp" && eval "$p") >/dev/null 2>&1 || { ok=0; echo "    postcheck failed: $p"; }; done
  if [ "$ok" = 1 ]; then pass=$((pass+1)); printf '  ok   %s\n' "$name"
  else fail=$((fail+1)); failed+=("$name"); printf '  FAIL %s\n' "$name"
       printf '%s\n' "$err" | sed 's/^/      stderr: /' | head -8; printf '%s\n' "$out" | sed 's/^/      stdout: /' | head -6; fi
  rm -rf "$tmp"
}

start=$(date +%s)
# cases are generated, not committed: tests/gen-cases.py is the source of truth
[ -f "$here/gen-cases.py" ] && python3 "$here/gen-cases.py" "$here/cases" >/dev/null
# inertness first, then everything else
for d in "$here"/cases/inertness/*/; do [ -d "$d" ] && [[ "${d#$here/cases/}" == $pattern/ || "$pattern" == "*/*" ]] && run_case "${d%/}"; done
for d in "$here"/cases/*/*/; do
  [ -d "$d" ] || continue
  case "$d" in */cases/inertness/*) continue ;; esac
  [[ "${d#$here/cases/}" == $pattern/ ]] || continue
  run_case "${d%/}"
done
echo
echo "passed $pass · failed $fail · $(( $(date +%s) - start ))s"
[ "$fail" = 0 ] || { printf '  - %s\n' "${failed[@]}"; exit 1; }
