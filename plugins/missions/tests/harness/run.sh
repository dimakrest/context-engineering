#!/bin/bash
# Harness smoke for the missions driver: ONE real worker run under claude or codex over the trace
# fixture, then the journal's shape, the run's verdict and the child's environment are checked. It
# costs money (claude: budget $2.00; codex has no budget flag, so the 900 s deadline is the bound)
# and needs the harness on PATH with its own auth, so the suites never run it -- an operator does,
# after a change to an adapter or to prep, to see that a real harness still produces what the stub
# does: dispatch -> agent_return -> cost (unit usd under claude, tokens under codex) -> step_done,
# a commit on the mission branch, and no push credential anywhere in the child's environment.
#
# usage: bash tests/harness/run.sh <claude|codex|both>
#
#   claude | codex   one adapter
#   both             both, then the two runs are compared: same journal shape, same outcome class,
#                    same evidence. This is the harness-agnostic claim (#6) as a test rather than
#                    a promise; the cost unit is the one difference and is exempt. `both` pays for
#                    both runs -- up to $2.00 of claude plus an unbudgeted codex run bounded only
#                    by the deadline -- and runs them in sequence, which the host lease requires.
#
# What it asserts, beyond the shape:
#   - the outcome class is one that means the worker did the work (never no_op, infra_crash,
#     stalled): a harness that launched, burned a dollar and produced nothing used to exit 0 here.
#     The purse must be big enough for the fixture feature -- at $0.50 a claude worker is cut off
#     mid-feature every time, and the run's class then says more about the budget than the adapter;
#   - the mission branch moved -- a commit is the evidence a handoff is graded against;
#   - the child's OWN environment carries no credential. The driver's `bin` is pointed at a shim
#     that dumps `os.environ` and then becomes the real binary, so this reads what the process
#     received, not what the driver believed it built (runs/*/env-names.txt is the latter, and a
#     bug between building the env and spawning would not show there).
#
# Exit 0 when all of that holds, 1 when the driver produced the wrong shape or verdict, and 2 when
# the harness could not run here at all -- not on PATH, or its own sandbox refused to start (codex
# needs a working bubblewrap; inside an unprivileged container every command fails before the shell
# and the run is a truthful `no_op`). That third code matters: "codex cannot run on this host" and
# "the codex adapter is broken" are different findings, and a smoke that conflates them gets
# dismissed as flaky. `both` compares only when both adapters actually ran.
#
# The tmp dir is kept and named -- a paid run's prompt, output and stderr are worth reading. The run
# takes the real host lease (~/.missions/host.lock) like any executor run; set MISSIONS_HOST_LOCK to
# run it beside a live mission.

set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
plugin=$(cd "$here/../.." && pwd)
h="${1:-}"
case "$h" in
  claude|codex|both) ;;
  *) echo "usage: bash tests/harness/run.sh <claude|codex|both>" >&2; exit 2 ;;
esac

# One adapter: $1 harness, $2 tmp dir. Leaves $2/summary.json for the comparison.
smoke_one() {
  local h="$1" tmp="$2" m rc start fail
  command -v "$h" >/dev/null 2>&1 || { echo "smoke: $h is not on PATH" >&2; return 2; }
  echo "smoke: harness $h  tmp $tmp"
  bash "$here/../traces/_base/repo.sh" "$tmp" || { echo "smoke: repo.sh failed" >&2; return 1; }
  m="$tmp/repo/.missions/demo"; mkdir -p "$m"
  cp -R "$here/../traces/_base/mission/." "$m/"
  bash "$plugin/bin/missions" init "$m" --harness "$h" || return 1

  # the shim: the driver launches this instead of the harness, so the environment it dumps is the
  # one the child process actually got
  mkdir -p "$tmp/bin"
  cat > "$tmp/bin/$h-shim" <<SHIM
#!/bin/bash
# missions smoke: dump this process's own environment, then become the real harness. The file is
# named env.txt because tests/traces/worker-env-stripped's stub writes the same thing under the
# same name -- the stub trace and the live smoke assert one property on one kind of evidence.
[ -n "\${MISSIONS_RUN_DIR:-}" ] && compgen -e | sort > "\$MISSIONS_RUN_DIR/env.txt"
exec $(command -v "$h") "\$@"
SHIM
  chmod +x "$tmp/bin/$h-shim"

  # a real worker on a small purse and a short leash, launched through the shim
  python3 - "$m/driver.json" "$h" "$tmp/bin/$h-shim" <<'EOF'
import json, sys
path, harness, shim = sys.argv[1:4]
with open(path, encoding="utf-8") as fh:
    cfg = json.load(fh)
cfg["roles"]["worker"].update({"timeout_s": 900, "budget_usd": 2.0})
cfg["adapters"][harness]["bin"] = shim
with open(path, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
EOF

  start=$(date +%s)
  ( cd "$tmp/repo" && GH_TOKEN="${GH_TOKEN:-smoke-canary}" SMOKE_SECRET_TOKEN=canary \
      bash "$plugin/bin/missions" run "$m" --limit 1 --until validate 2>&1 ) | tee "$tmp/run.log"
  rc=${PIPESTATUS[0]}
  echo "smoke: missions run exited $rc after $(( $(date +%s) - start ))s"

  python3 - "$m" "$h" "$tmp/repo" "$tmp/summary.json" "$plugin" <<'EOF'

import json, re, subprocess, sys
from pathlib import Path
mdir, harness, repo, summary_path, plugin = (Path(sys.argv[1]), sys.argv[2], sys.argv[3],
                                             sys.argv[4], sys.argv[5])
# the driver's own vocabulary, so a renamed or added class cannot leave this judging by a stale
# literal. The complement is what the check is for: these four mean nothing happened.
sys.path.insert(0, str(Path(plugin) / "driver"))
from missions.outcome import CLASSES
NOTHING_HAPPENED = ("no_op", "infra_crash", "stalled", "infra_quota")
want_unit = {"claude": "usd", "codex": "tokens"}[harness]
recs = [json.loads(ln) for ln in (mdir / "journal.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
mine = [r for r in recs if r.get("task") == "F001#1"
        and r.get("event") in ("dispatch", "agent_return", "cost", "step_done")]
seq = [r["event"] for r in mine]
by = {r["event"]: r for r in mine}
d, a, c, s = (by.get(k, {}) for k in ("dispatch", "agent_return", "cost", "step_done"))
ok = True
def check(what, cond):
    global ok
    print("  %s %s" % ("ok  " if cond else "FAIL", what))
    ok = ok and bool(cond)

check("journal shape for F001#1: %s" % (" -> ".join(seq) or "no records"),
      seq == ["dispatch", "agent_return", "cost", "step_done"])
check("dispatch names harness %s and agent mission-worker" % harness,
      d.get("harness") == harness and d.get("agent") == "mission-worker")
check("cost unit is %s (got %r from %r)" % (want_unit, c.get("unit"), c.get("source")),
      c.get("unit") == want_unit)
check("step_done carries a class and elapsed_s",
      isinstance(s.get("cls"), str) and isinstance(s.get("elapsed_s"), (int, float)))

# the worker did the work: a class that means nothing happened is the failure this smoke exists for
cls = s.get("cls")
# evidence: the mission branch moved
log = subprocess.run(["git", "-C", repo, "log", "--oneline", "main..mission/demo"],
                     capture_output=True, text=True).stdout.strip()

# before judging the verdict: did the harness's own sandbox refuse to start? Then nothing about
# the driver was established, and saying so is the only honest result (exit 2, not a failure).
# `output.md` is deliberately NOT read: it is the agent's own last message, and grade.py's
# quota_signature refuses that channel ("a test named after a rate limit is not a rate limit").
# stderr and the harness's structured stdout are where the tool speaks for itself.
run_dir = mdir / "runs" / "F001#1"
blob = "\n".join((run_dir / n).read_text(encoding="utf-8", errors="replace")
                 for n in ("stderr", "stdout") if (run_dir / n).exists())
# kept tight on purpose: these are the tool's own error forms, not prose about them. The
# character class stops at the quote or backtick that ends the message inside codex's JSON.
blocked = re.search(r"(bwrap: [^\n\"`\\]{0,70}|landlock[^\n\"`]{0,40}not permitted|"
                    r"seccomp[^\n\"`]{0,40}not permitted|"
                    r"sandbox[^\n\"`]{0,40}(?:failed to start|startup failure))", blob, re.I)
if blocked and not log:
    print("  --   %s could not run here: %s" % (harness, blocked.group(0).strip()[:120]))
    print("smoke: NOT ESTABLISHED -- the harness's sandbox refused to start; nothing was proved "
          "about the driver")
    sys.exit(2)

check("outcome class %r is one the driver defines" % cls, cls in CLASSES)
check("outcome class %r means the worker did the work" % cls, cls not in NOTHING_HAPPENED)
check("a commit landed on mission/demo (%s)" % (log.splitlines()[0] if log else "none"), bool(log))

# the credential canary, read from the environment the child process itself had
child = run_dir / "env.txt"
names = child.read_text(encoding="utf-8").split() if child.exists() else None
check("the child's own environment was captured (%s)" % child.name, names is not None)
if names is not None:
    # the names tests/traces/worker-env-stripped asserts on the stub, plus this script's canary
    leaked = [n for n in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
                          "MY_SECRET", "FOO_TOKEN", "ANTHROPIC_API_KEY", "SMOKE_SECRET_TOKEN") if n in names]
    check("no credential in the child's %d environment names (%s)" % (
        len(names), ", ".join(leaked) or "none leaked"), not leaked)
    # GH_CONFIG_DIR must be PRESENT: the driver sets it to an empty config dir of its own, which
    # is what makes `gh` find no login. Its absence would let gh fall back to the operator's.
    check("the child got the mission's own variables (MISSIONS_TASK, GIT_CONFIG_GLOBAL, GH_CONFIG_DIR)",
          all(n in names for n in ("MISSIONS_TASK", "GIT_CONFIG_GLOBAL", "GH_CONFIG_DIR")))

print("smoke: class %s · cost %s %s · elapsed %ss · model %s" % (
    cls, c.get("value"), c.get("unit"), s.get("elapsed_s"), a.get("model")))
Path(summary_path).write_text(json.dumps({
    "harness": harness, "seq": seq, "cls": cls, "commit": bool(log),
    "handoff": (mdir / "handoffs" / "F001.md").exists(),
    "cost_unit": c.get("unit"), "model": a.get("model"),
}, indent=2) + "\n", encoding="utf-8")
sys.exit(0 if ok else 1)
EOF
  fail=$?
  echo "smoke: kept $tmp (prompt, output and stderr under $m/runs/F001#1/)"
  return $fail
}

if [ "$h" != both ]; then
  tmp=$(mktemp -d); smoke_one "$h" "$tmp"; exit $?
fi

# both: each adapter on its own copy of the fixture, then the two verdicts are compared
rc_c=0; rc_x=0
tmp_c=$(mktemp -d); smoke_one claude "$tmp_c" || rc_c=$?
echo
tmp_x=$(mktemp -d); smoke_one codex "$tmp_x" || rc_x=$?
echo
if [ "$rc_c" = 2 ] || [ "$rc_x" = 2 ]; then
  echo "smoke: not comparing -- could not run on this host:$(
    [ "$rc_c" = 2 ] && printf ' claude'; [ "$rc_x" = 2 ] && printf ' codex')"
  echo "smoke: the harness-agnostic claim is UNTESTED here, not disproved."
  exit 2
fi
echo "smoke: comparing the two runs"
python3 - "$tmp_c/summary.json" "$tmp_x/summary.json" <<'EOF'
import json, sys
from pathlib import Path
a, b = (json.loads(Path(p).read_text(encoding="utf-8")) if Path(p).exists() else None for p in sys.argv[1:3])
if a is None or b is None:
    print("  FAIL one of the runs produced no summary; nothing to compare"); sys.exit(1)
ok = True
def same(field):
    global ok
    hit = a[field] == b[field]
    print("  %s %s: claude %r · codex %r" % ("ok  " if hit else "FAIL", field, a[field], b[field]))
    ok = ok and hit
same("seq")          # the journal shape
same("cls")          # the verdict
same("commit")       # the evidence
same("handoff")
print("  --   cost_unit differs by design: claude %r · codex %r" % (a["cost_unit"], b["cost_unit"]))
print("smoke: the same trace %s under both adapters" % ("agrees" if ok else "DISAGREES"))
sys.exit(0 if ok else 1)
EOF
cmp_rc=$?
[ "$rc_c" = 0 ] && [ "$rc_x" = 0 ] && [ "$cmp_rc" = 0 ]
