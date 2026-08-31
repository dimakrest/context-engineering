#!/bin/bash
# The five acceptance metrics for the missions workflow, from one journal.
#
# usage: journal-metrics.sh <mission-dir>
#
# These are how you know the fixes bit. The next retro should be this command,
# not an eight-agent forensic pass. Baselines (analytics-hour-filter, 2026-08):
#   M1 follow-ups / features 1.46 rising    M2 idle ~85%, 0 resumes / 10 compactions
#   M3 briefing ~22 KB, state 1,182 lines   M4 3 disclosures, cumulative diffs
#   M5 7/141 dispatches with cost, 3 cap raises
# Anything not recorded is printed as "not recorded" -- never interpolated.

set -uo pipefail
m="${1:-}"
[ -d "$m" ] && [ -f "$m/journal.jsonl" ] || { echo "usage: journal-metrics.sh <mission-dir>" >&2; exit 1; }

python3 - "$m" <<'PY'
import json, re, sys, pathlib, datetime as dt, collections
M = pathlib.Path(sys.argv[1])
ev = []
for line in (M / "journal.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
    try: ev.append(json.loads(line))
    except Exception: pass
def ts(e):
    s = e.get("ts", "")
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            d = dt.datetime.strptime(s, fmt)
            return d.replace(tzinfo=dt.timezone.utc) if d.tzinfo is None else d
        except Exception: pass
    return None
times = [t for t in (ts(e) for e in ev) if t]
n = collections.Counter(e.get("event") for e in ev)
rd = lambda f: (M / f).read_text(encoding="utf-8", errors="replace") if (M / f).exists() else ""
n_feat = len(re.findall(r'(?m)^###\s+F\d{3}\b', rd("features.md")))
n_fu = len(re.findall(r'(?m)^##\s+FU\d{3}\b', rd("followups.md")))
print(f"M1 bound     follow-ups/features: {n_fu}/{n_feat} = {n_fu/n_feat if n_feat else 0:.2f}"
      f" · behavior validations: {sum(1 for e in ev if e.get('event')=='dispatch' and 'behavior' in str(e.get('agent')))}")
idle = 0.0; span = 0.0
if len(times) > 1:
    times.sort(); span = (times[-1] - times[0]).total_seconds()
    idle = sum(max(0, (b - a).total_seconds() - 1800) for a, b in zip(times, times[1:]))
halts = collections.Counter(e.get("class", "unclassified") for e in ev if e.get("event") == "halt")
print(f"M2 halts     idle share (gaps>30m): {idle/span*100 if span else 0:.0f}% of {span/3600:.0f}h"
      f" · halts by class: {dict(halts) or 'none'} · resumes: {n.get('resume', 0)}")
briefs = [e.get("briefing_bytes") for e in ev if e.get("event") == "dispatch" and e.get("briefing_bytes")]
lines = sum(1 for _ in (M / "state.md").open(encoding="utf-8", errors="replace")) if (M / "state.md").exists() else 0
print(f"M3 state     briefing bytes: {('max %d' % max(briefs)) if briefs else 'not recorded'}"
      f" · state.md lines now: {lines} · writer-lock clears: {n.get('writer_lock_cleared', 0)}")
patches = sorted((M / "patches").glob("F*.patch")) if (M / "patches").exists() else []
print(f"M4 review    reviewer disclosures: {n.get('reviewer_disclosure', 0)} · patch files: {len(patches)}"
      f" · lease waits: {n.get('lease_wait', 0)}")
rets = [e for e in ev if e.get("event") in ("agent_return", "agent_stopped")]
withdur = sum(1 for e in rets if isinstance(e.get("duration_s"), (int, float)))
costs = {e.get("session_id"): e.get("usd") for e in ev if e.get("event") == "session_cost"}
print(f"M5 spend     returns with duration: {withdur}/{len(rets)} · sessions with cost: {len(costs)}"
      f" · total journaled usd: {('%.2f' % sum(v for v in costs.values() if isinstance(v,(int,float)))) if costs else 'not recorded'}"
      f" · cap raises: {n.get('cap_raised', 0)}")
# Seats: which model each dispatch actually ran on (journaled by the hooks from the
# Agent call's override, else the agent definition). Unrecorded dispatches predate v0.2.
by_model = collections.Counter(e.get("model") or "not recorded" for e in ev if e.get("event") == "dispatch")
dur_model = collections.defaultdict(float)
for e in rets:
    if isinstance(e.get("duration_s"), (int, float)): dur_model[e.get("model") or "not recorded"] += e["duration_s"]
print(f"seats        dispatches by model: {dict(by_model) or 'not recorded'}"
      f" · agent-hours by model: {({k: round(v/3600, 2) for k, v in dur_model.items()}) or 'not recorded'}")
PY
