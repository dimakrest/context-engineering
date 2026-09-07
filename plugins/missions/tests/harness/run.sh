#!/bin/bash
# Harness smoke for the missions driver: ONE real worker run under claude or codex over the trace
# fixture, then the journal's shape is checked. It costs money (budget $0.50, deadline 600 s) and
# needs the harness on PATH with its own auth, so the suites never run it -- an operator does,
# after a change to an adapter or to prep, to see that a real harness still produces the shape the
# stub does: dispatch -> agent_return -> cost (unit usd under claude, tokens under codex) ->
# step_done, and that no push credential reached the run (GH_TOKEN is set as a canary when the
# operator has none, and must be absent from runs/F001#1/env-names.txt).
#
# usage: bash tests/harness/run.sh <claude|codex>
#
# Exit 0 when the shape holds, whatever the outcome class was: a worker that failed its task is
# still a shape-true run, and the class, cost and elapsed time are printed. The tmp dir is kept and
# named -- a paid run's prompt, output and stderr are worth reading. The run takes the real host
# lease (~/.missions/host.lock) like any executor run; set MISSIONS_HOST_LOCK to run it beside a
# live mission.

set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
plugin=$(cd "$here/../.." && pwd)
h="${1:-}"
case "$h" in
  claude|codex) ;;
  *) echo "usage: bash tests/harness/run.sh <claude|codex>" >&2; exit 2 ;;
esac
command -v "$h" >/dev/null 2>&1 || { echo "smoke: $h is not on PATH" >&2; exit 2; }

tmp=$(mktemp -d)
echo "smoke: harness $h  tmp $tmp"
bash "$here/../traces/_base/repo.sh" "$tmp" || { echo "smoke: repo.sh failed" >&2; exit 1; }
m="$tmp/repo/.missions/demo"; mkdir -p "$m"
cp -R "$here/../traces/_base/mission/." "$m/"
bash "$plugin/bin/missions" init "$m" --harness "$h" || exit 1
# a real worker on a small purse and a short leash
python3 - "$m/driver.json" <<'EOF'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    cfg = json.load(fh)
cfg["roles"]["worker"].update({"timeout_s": 600, "budget_usd": 0.5})
with open(path, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
EOF

start=$(date +%s)
GH_TOKEN="${GH_TOKEN:-smoke-canary}" bash "$plugin/bin/missions" run "$m" --limit 1 --until validate 2>&1 | tee "$tmp/run.log"
rc=${PIPESTATUS[0]}
echo "smoke: missions run exited $rc after $(( $(date +%s) - start ))s"

fail=0
python3 - "$m/journal.jsonl" "$h" <<'EOF' || fail=1
import json, sys
path, harness = sys.argv[1], sys.argv[2]
want_unit = {"claude": "usd", "codex": "tokens"}[harness]
with open(path, encoding="utf-8") as fh:
    recs = [json.loads(ln) for ln in fh if ln.strip()]
mine = [r for r in recs if r.get("task") == "F001#1" and r.get("event") in ("dispatch", "agent_return", "cost", "step_done")]
seq = [r["event"] for r in mine]
ok = True
def check(what, cond):
    global ok
    print("  %s %s" % ("ok  " if cond else "FAIL", what))
    ok = ok and cond
check("journal shape for F001#1: %s" % (" -> ".join(seq) or "no records"), seq == ["dispatch", "agent_return", "cost", "step_done"])
by = {r["event"]: r for r in mine}
d, a, c, s = by.get("dispatch", {}), by.get("agent_return", {}), by.get("cost", {}), by.get("step_done", {})
check("dispatch names harness %s and agent mission-worker" % harness, d.get("harness") == harness and d.get("agent") == "mission-worker")
check("cost unit is %s (got %r from %r)" % (want_unit, c.get("unit"), c.get("source")), c.get("unit") == want_unit)
check("step_done carries a class and elapsed_s", isinstance(s.get("cls"), str) and isinstance(s.get("elapsed_s"), (int, float)))
print("smoke: class %s · cost %s %s · elapsed %ss · model %s" % (
    s.get("cls"), c.get("value"), c.get("unit"), s.get("elapsed_s"), a.get("model")))
sys.exit(0 if ok else 1)
EOF
names="$m/runs/F001#1/env-names.txt"
if [ -f "$names" ] && ! grep -qx GH_TOKEN "$names"; then
  echo "  ok   no GH_TOKEN in the run's environment ($names)"
else
  echo "  FAIL GH_TOKEN reached the run, or $names is missing"; fail=1
fi
echo "smoke: kept $tmp (prompt, output and stderr under $m/runs/F001#1/)"
exit $fail
