#!/bin/bash
# Convergence and proof-class gate. Run in VALIDATE step 4 before advancing.
#
# usage: mission-converge.sh <mission-dir> [M<n>]
#
# The last mission filed 82 follow-ups against 56 features and the ratio rose
# every milestone (0.25 -> 1.32); nothing in the loop watched it, and the cap was
# raised twice instead. Three checks, all computed from the mission files:
#   1. cumulative follow-ups > features                    -> "follow-ups exceed features"
#   2. per-milestone ratio rising two milestones running   -> "ratio rising"
#   3. a milestone that introduces interface/conversational assertions and has
#      none of that class proven                          -> "proof class introduced, none proven"
# Exit 0 = converging. Exit 2 = halt (class: block) and re-plan; never raise a cap.

set -uo pipefail
m="${1:-}"; ms="${2:-}"
[ -d "$m" ] && [ -f "$m/features.md" ] || { echo "usage: mission-converge.sh <mission-dir> [M<n>]" >&2; exit 1; }

python3 - "$m" "$ms" <<'PY'
import re, sys, pathlib, collections
M = pathlib.Path(sys.argv[1]); ms = sys.argv[2]
rd = lambda n: (M / n).read_text(encoding="utf-8", errors="replace") if (M / n).exists() else ""
features, followups, contract = rd("features.md"), rd("followups.md"), rd("contract.md")
problems = []

# --- features per milestone (### F00n under ## M<n>)
feat_ms = {}; cur = None
for line in features.splitlines():
    h = re.match(r'^##\s+(M\d+[a-z]?)\b', line)
    if h: cur = h.group(1); continue
    f = re.match(r'^###\s+(F\d{3})\b', line)
    if f and cur: feat_ms[f.group(1)] = cur
n_feat = len(feat_ms)

# --- follow-ups per milestone: "(from M1-review-F003)" / "(from M2-scrutiny)" tags, else unknown
fu_entries = re.findall(r'(?m)^##\s+(FU\d{3})\s+—\s+(.*)$', followups)
fu_ms = collections.Counter()
for _id, title in fu_entries:
    t = re.search(r'\(from\s+(M\d+)[a-z]?[-\s]', title)
    fu_ms[t.group(1) if t else "?"] += 1
n_fu = len(fu_entries)

if n_feat and n_fu > n_feat:
    problems.append(f"follow-ups exceed features: {n_fu} FU vs {n_feat} F (cumulative)")

# --- ratio trend across milestones (base milestone id, M1b folds into M1)
base = lambda x: re.match(r'M\d+', x).group(0)
feat_per = collections.Counter(base(v) for v in feat_ms.values())
order = sorted(feat_per, key=lambda x: int(x[1:]))
ratios = [(mm, fu_ms.get(mm, 0) / feat_per[mm]) for mm in order if feat_per[mm]]
if len(ratios) >= 3:
    (_, a), (_, b), (_, c) = ratios[-3:]
    if b > a and c > b:
        problems.append("ratio rising two milestones running: " + " -> ".join(f"{mm} {r:.2f}" for mm, r in ratios[-3:]))

# --- proof classes introduced vs proven, for the milestone under validation
if ms:
    rows = re.findall(r'(?m)^\|\s*(A\d{3}[a-z]?)\s*\|.*?\|\s*(structural|interface|conversational)\s*\|([^|]*)\|([^|]*)\|', contract)
    by_class = collections.defaultdict(list)
    for aid, cls, feats, status in rows:
        fids = re.findall(r'F\d{3}', feats)
        if any(base(feat_ms.get(f, "M0")) == base(ms) for f in fids):
            by_class[cls].append(bool(re.match(r'^\s*[*`_]*\s*proven\b', status, re.I)))
    for cls in ("interface", "conversational"):
        if by_class.get(cls) and not any(by_class[cls]):
            problems.append(f"proof class introduced, none proven: {len(by_class[cls])} {cls} assertion(s) in {ms}, 0 proven")

print(f"features {n_feat} · follow-ups {n_fu} · ratio {n_fu / n_feat if n_feat else 0:.2f} · per milestone " +
      ", ".join(f"{mm} {r:.2f}" for mm, r in ratios))
if problems:
    print("CONVERGE FAIL — halt (class: block) and re-plan; do not raise a cap:")
    for p in problems: print(" -", p)
    sys.exit(2)
print("CONVERGE PASS")
PY
