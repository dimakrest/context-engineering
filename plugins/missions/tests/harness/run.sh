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
#
# MISSIONS_SMOKE_CODEX_SANDBOX overrides adapters.codex.sandbox for the run. On a host that
# refuses unprivileged user namespaces codex's bubblewrap cannot start, and preflight now refuses
# the run; `MISSIONS_SMOKE_CODEX_SANDBOX=danger-full-access` is how an operator says "this host is
# already a sandbox" and lets the smoke exercise the adapter. A deliberate choice, not a default:
# it leaves the driver's own env whitelist, git hooks, blindness and post-exit grade as the only
# enforcement.
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

for x in $([ "$h" = both ] && echo claude codex || echo "$h"); do
  command -v "$x" >/dev/null 2>&1 || { echo "smoke: $x is not on PATH" >&2; exit 2; }
done

# One adapter: $1 harness, $2 tmp dir. Leaves $2/summary.json for the comparison.
smoke_one() {
  local h="$1" tmp="$2" m rc start fail real
  real=$(command -v "$h")
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
# missions smoke: record this process's own environment, then become the real harness. env.txt is
# `env | cut -d= -f1` -- byte for byte what tests/traces/worker-env-stripped's stub writes, so the
# stub trace and the live smoke assert one property on one kind of evidence. NOT `compgen -e`,
# which enumerates bash's exported *identifiers* and so cannot see a credential exported under a
# name that is not a valid shell identifier. redirects.txt carries the values the canary has to
# JUDGE rather than merely find: GH_CONFIG_DIR, GIT_ASKPASS and GIT_SSH_COMMAND are stripped from
# the operator's environment and then re-set by the driver to its own, so their presence proves
# nothing and their absence would be a different bug -- only the value says which.
if [ -n "\${MISSIONS_RUN_DIR:-}" ]; then
  env | cut -d= -f1 | sort > "\$MISSIONS_RUN_DIR/env.txt"
  { printf 'GH_CONFIG_DIR=%s\n' "\${GH_CONFIG_DIR:-}"
    printf 'GH_ENTRIES=%s\n' "\$(ls -A "\${GH_CONFIG_DIR:-/nonexistent}" 2>/dev/null | wc -l)"
    printf 'GIT_ASKPASS=%s\n' "\${GIT_ASKPASS:-}"
    printf 'GIT_SSH_COMMAND=%s\n' "\${GIT_SSH_COMMAND:-}"
  } > "\$MISSIONS_RUN_DIR/redirects.txt"
fi
exec "$real" "\$@"
SHIM
  chmod +x "$tmp/bin/$h-shim"

  # a real worker on a small purse and a short leash, launched through the shim
  python3 - "$m/driver.json" "$h" "$tmp/bin/$h-shim" <<'EOF' || {
import json, os, sys
path, harness, shim = sys.argv[1:4]
with open(path, encoding="utf-8") as fh:
    cfg = json.load(fh)
# codex declares no budget capability, so a budget_usd there is a number the driver prints and
# never enforces; its bound is the deadline
cfg["roles"]["worker"]["timeout_s"] = 900
if harness == "claude":
    cfg["roles"]["worker"]["budget_usd"] = 2.0
else:
    cfg["roles"]["worker"]["budget_usd"] = None
    sandbox = os.environ.get("MISSIONS_SMOKE_CODEX_SANDBOX")
    if sandbox:
        cfg["adapters"]["codex"]["sandbox"] = sandbox
cfg["adapters"][harness]["bin"] = shim
with open(path, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
EOF
    echo "smoke: could not cap the purse or install the shim in driver.json" >&2; return 1; }

  start=$(date +%s)
  # every one of these is on prep.NEVER_EXACT, or falls off the whitelist, so every one MUST be
  # absent from the child. A canary that is never planted is a check that cannot fail.
  ( cd "$tmp/repo" && \
    GH_TOKEN="${GH_TOKEN:-smoke-canary}" GITHUB_TOKEN=canary GH_ENTERPRISE_TOKEN=canary \
    GITHUB_ENTERPRISE_TOKEN=canary MISSIONS_PUSH_TOKEN=canary SSH_AUTH_SOCK=/tmp/smoke-agent.sock \
    GIT_ASKPASS=/bin/true SMOKE_SECRET_TOKEN=canary SMOKE_SECRET_KEY=canary \
    GH_CONFIG_DIR=/tmp/smoke-operator-gh \
      bash "$plugin/bin/missions" run "$m" --limit 1 --until validate 2>&1 ) | tee "$tmp/run.log"
  rc=${PIPESTATUS[0]}
  echo "smoke: missions run exited $rc after $(( $(date +%s) - start ))s"
  if [ "$rc" = 2 ]; then
    # preflight-failed: the driver refused before spending anything, which is the cheap version of
    # "could not run here". Its own problem line above already names the fix.
    echo "smoke: NOT ESTABLISHED -- preflight refused this host; nothing was spent and nothing"
    echo "       was proved about the driver."
    echo "smoke: kept $tmp"
    return 2
  fi

  python3 - "$m" "$h" "$tmp/repo" "$tmp/summary.json" "$plugin" "$rc" <<'EOF'

import json, re, subprocess, sys
from pathlib import Path
mdir, harness, repo, summary_path, plugin = (Path(sys.argv[1]), sys.argv[2], sys.argv[3],
                                             sys.argv[4], sys.argv[5])
driver_rc = int(sys.argv[6])
# the driver's own vocabulary, partitioned. The partition is ASSERTED against CLASSES below, so a
# ninth class fails loudly here instead of silently landing in whichever bucket the code guessed.
sys.path.insert(0, str(Path(plugin) / "driver"))
from missions.outcome import CLASSES
PRODUCTIVE = ("done", "handoff_missing", "malformed_handoff", "tests_failed")
NOTHING_HAPPENED = ("no_op", "infra_crash", "stalled")
NOT_OUR_FAULT = ("infra_quota",)   # the provider said no; the driver handled it (exit 8)
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
check("missions run exited %d (limit-reached)" % driver_rc, driver_rc == 3)
check("the class partition covers outcome.CLASSES (%d)" % len(CLASSES),
      set(PRODUCTIVE) | set(NOTHING_HAPPENED) | set(NOT_OUR_FAULT) == set(CLASSES))

cls = s.get("cls")
run_dir = mdir / "runs" / "F001#1"

# The credential checks come FIRST and unconditionally: the shim wrote them before it exec'd the
# harness, so they hold even when the harness went on to do nothing. This is the one property that
# survives a dead harness, and on a host where codex cannot start it is the only one there is.
child = run_dir / "env.txt"
names = child.read_text(encoding="utf-8").split() if child.exists() else None
check("the child's own environment was captured (%s)" % child.name, names is not None)
if names is not None:
    # exactly the names planted before the run, every one of them on prep.NEVER_EXACT or off the
    # whitelist. The harness's OWN key (ANTHROPIC_API_KEY, OPENAI_API_KEY) is forwarded by design
    # via prep.HARNESS_ENV and is not a leak -- asserting its absence would fail a correct driver.
    # GIT_ASKPASS, GIT_SSH_COMMAND and GH_CONFIG_DIR are NOT here: the driver strips the
    # operator's and sets its own, so they must be PRESENT -- judged by value, just below.
    planted = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
               "MISSIONS_PUSH_TOKEN", "SSH_AUTH_SOCK", "SMOKE_SECRET_TOKEN", "SMOKE_SECRET_KEY")
    leaked = [n for n in planted if n in names]
    check("none of the %d planted credentials reached the child (%s)" % (
        len(planted), ", ".join(leaked) or "none leaked"), not leaked)
    check("the child got the mission's own variables (MISSIONS_TASK, GIT_CONFIG_GLOBAL)",
          all(n in names for n in ("MISSIONS_TASK", "GIT_CONFIG_GLOBAL")))
# The redirections are judged by VALUE: the operator's were planted, and the child must hold the
# driver's own instead -- an empty gh config so `gh pr create` finds no login, and an askpass that
# refuses so git can neither prompt for a credential nor open a transport that would use one.
red = run_dir / "redirects.txt"
if red.exists():
    kv = dict(ln.split("=", 1) for ln in red.read_text(encoding="utf-8").splitlines() if "=" in ln)
    check("GH_CONFIG_DIR is the driver's own empty dir, not the operator's (%s, %s entries)" % (
        kv.get("GH_CONFIG_DIR") or "unset", kv.get("GH_ENTRIES")),
        (kv.get("GH_CONFIG_DIR") or "").endswith("/githooks/gh") and kv.get("GH_ENTRIES") == "0")
    check("GIT_ASKPASS and GIT_SSH_COMMAND point at the driver's no-credentials script",
          all((kv.get(k) or "").endswith("/githooks/no-credentials")
              for k in ("GIT_ASKPASS", "GIT_SSH_COMMAND")))
else:
    check("the child's driver-set redirections were captured (redirects.txt)", False)

# evidence: the mission branch moved
log = subprocess.run(["git", "-C", repo, "log", "--oneline", "main..mission/demo"],
                     capture_output=True, text=True).stdout.strip()

# Could the harness run here at all? Two ways it could not, neither of them the driver's fault.
# A definite failure above DOMINATES: exit 2 says "nothing was established", which would be a lie
# if something already failed.
#
# stderr ONLY, deliberately. The case this was written for -- codex's bubblewrap refusing to start
# -- is now caught structurally by CodexAdapter.preflight_problems() before a token is spent, so
# this is only a backstop for a harness that fails a way preflight did not predict. And stdout
# would be a bad backstop: measured over three codex runs, every `bwrap:` occurrence was inside an
# `agent_message` item and none in any other channel or in stderr, so reading stdout buys no
# detection codex does not already give the agent's prose -- it only adds the way this check can
# LIE, an agent that quotes a sandbox error into a false "nothing was established". grade.py's
# quota_signature draws the same line for the same reason: "a test named after a rate limit is not
# a rate limit".
TAIL = 2_000_000        # adapters.base.read_output's cap, for the same reason
blob = "\n".join((run_dir / n).read_text(encoding="utf-8", errors="replace")[-TAIL:]
                 for n in ("stderr",) if (run_dir / n).exists())
blocked = re.search(r"(bwrap: [^\n\"`\\]{0,70}|landlock[^\n\"`]{0,40}not permitted|"
                    r"seccomp[^\n\"`]{0,40}not permitted|"
                    r"sandbox[^\n\"`]{0,40}(?:failed to start|startup failure))", blob, re.I)
if (blocked and not log) or cls in NOT_OUR_FAULT:
    why = ("%s could not run here: %s" % (harness, blocked.group(0).strip()[:120])) if blocked else (
        "the provider reported a quota or limit; the driver stopped correctly")
    print("  --   " + why)
    print("smoke: NOT ESTABLISHED -- nothing was proved about the driver%s" % (
        "" if ok else ", AND checks above already failed"))
    sys.exit(1 if not ok else 2)

check("outcome class %r is one the driver defines" % cls, cls in CLASSES)
check("outcome class %r means the worker did the work" % cls, cls in PRODUCTIVE)
check("a commit landed on mission/demo (%s)" % (log.splitlines()[0] if log else "none"), bool(log))

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
if [ "$rc_c" = 1 ] || [ "$rc_x" = 1 ]; then
  # a paid run that actually failed is a finding; do not let the other adapter's "cannot run here"
  # downgrade it to a code an operator or CI wrapper reads as "not applicable"
  echo "smoke: not comparing -- a run FAILED:$(
    [ "$rc_c" = 1 ] && printf ' claude'; [ "$rc_x" = 1 ] && printf ' codex')"
  exit 1
fi
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
