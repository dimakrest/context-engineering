#!/bin/bash
# Coherence gate for /missions:mission-amend. Run after every amendment, before reporting.
#
# usage: check.sh <mission-dir>
#
# An amendment is a set of edits across five files that all describe the same
# thing. The failure mode is not a bad edit -- it is a *partial* one: a feature
# deleted from its own section but still named in a Depends-on line, an
# assertion a feature claims that the contract routes somewhere else. Each file
# reads correctly on its own, so nothing catches it except comparing them.
#
# Coverage is checked in BOTH directions on purpose. A one-directional version
# of this check passed a real mission whose A002 was claimed by F001 while the
# contract routed it to F002 alone.
#
# Exit 0 = the mission directory is internally consistent. Exit 1 = do not report
# the amendment as done.

set -uo pipefail

mission="${1:-}"
[ -n "$mission" ] && [ -d "$mission" ] || {
  echo "CHECK FAIL: no mission directory at '$mission'"; exit 1; }

python3 - "$mission" <<'PY'
import re, sys, pathlib, collections

M = pathlib.Path(sys.argv[1])
errs, notes = [], []

def read(name):
    p = M / name
    return p.read_text() if p.exists() else None

contract = read("contract.md")
features = read("features.md")
design   = read("design.md")
state    = read("state.md")

if contract is None or features is None:
    print("CHECK FAIL: contract.md and features.md are both required")
    sys.exit(1)

def amendments_text(text):
    """The body of any '## Amendments' section."""
    lines, inside, out = text.splitlines(), False, []
    for l in lines:
        if l.startswith("## "):
            inside = l.startswith("## Amendments")
        if inside:
            out.append(l)
    return "\n".join(out)

# ---- contract: assertion -> (class, [features]) ------------------------------
# Columns are located by the header row, not by position: the old regex took
# "whatever column follows Proof class" as Feature(s), so inserting a column
# there silently broke routing. Rows whose class cell is not a proof class
# (retired ~~A005~~ rows, separators) are skipped.
CLASSES = ("structural", "interface", "conversational")
# What the Agent call's `model:` accepts. `inherit` is frontmatter-only vocabulary and
# would be rejected on the call -- after the lock was taken.
SEATS = ("sonnet", "opus", "haiku", "fable")

def seat_problem(text, where):
    """Validates the seat named at the head of a line (a rationale may follow an em dash,
    a paren or a # comment, as on Depends-on). None when the seat is fine or absent."""
    seat = re.split(r'[—(#]', text, maxsplit=1)[0].strip().strip("`* ")
    if seat in ("", "-", "none"):            # the Depends-on "none" convention: same as omitting the line
        return None
    if seat in SEATS or re.fullmatch(r'claude-[a-z0-9.-]+', seat):
        return None
    return f"{where} names seat '{seat}'; a seat is one of {', '.join(SEATS)} or a full claude-… model id"
ct, ct_class, ct_budget, cols = {}, {}, {}, {}
for line in contract.splitlines():
    if not line.lstrip().startswith("|"):
        continue
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if not cols:
        low = [c.lower() for c in cells]
        if "id" in low and any("proof class" in c for c in low):
            for i, c in enumerate(low):
                cols[c] = i
            cols["_class"] = next(i for i, c in enumerate(low) if "proof class" in c)
            cols["_feat"] = next((i for i, c in enumerate(low) if c.startswith("feature")), None)
            cols["_budget"] = next((i for i, c in enumerate(low) if "proof budget" in c), None)
        continue
    m = re.match(r'^(A\d{3}[a-z]?)$', cells[cols["id"]]) if len(cells) > cols["id"] else None
    if not m or len(cells) <= cols["_class"] or cells[cols["_class"]].strip("`* ") not in CLASSES:
        continue
    aid = m.group(1)
    ct_class[aid] = cells[cols["_class"]].strip("`* ")
    feats_cell = cells[cols["_feat"]] if cols["_feat"] is not None and len(cells) > cols["_feat"] else ""
    ct[aid] = [f for f in re.findall(r'F\d{3}', feats_cell)]
    if cols["_budget"] is not None and len(cells) > cols["_budget"]:
        ct_budget[aid] = cells[cols["_budget"]]
if not cols:
    print("CHECK FAIL: contract.md has no assertion table header (| ID | ... | Proof class | ...)")
    sys.exit(1)

# ---- features: coverage table, per-feature lists, dependencies ---------------
cov, own, deps, files, repairs, cur = {}, {}, {}, {}, set(), None
for line in features.splitlines():
    m = re.match(r'###\s+(F\d{3})\b', line)
    if m:
        cur = m.group(1); continue
    m = re.match(r'\|\s*(F\d{3})\s*\|([^|]+)\|', line)
    if m:
        cov[m.group(1)] = [a.strip() for a in m.group(2).split(',') if a.strip()]
        continue
    if cur:
        m = re.match(r'-\s*\*\*Assertions:\*\*\s*(.+)', line)
        if m:
            own[cur] = [a.strip() for a in m.group(1).split(',') if a.strip()]
        m = re.match(r'-\s*\*\*Files:\*\*\s*(.+)', line)
        if m:
            files[cur] = [x.strip("` ") for x in re.split(r'[,\s]+', m.group(1)) if x.strip("` ") and "/" in x or x.strip("` ").endswith((".py", ".ts", ".tsx", ".md", ".sh", ".json"))]
        if re.match(r'-\s*\*\*Repairs:\*\*', line):
            repairs.add(cur)
        m = re.match(r'-\s*\*\*Seat:\*\*\s*(.+)', line)
        if m:
            # A seat is passed verbatim as `model:` on the Agent call, so it must
            # be something the harness accepts -- a typo here is a dispatch that
            # silently runs on the wrong model, or fails after the lock was taken.
            p = seat_problem(m.group(1), cur)
            if p: errs.append(p)
        m = re.match(r'-\s*\*\*Depends on:\*\*\s*(.+)', line)
        if m:
            # Ids only from the head of the line. A Depends-on may carry its
            # rationale after an em dash or in parentheses, and that prose
            # routinely names the feature itself ("F003's DST assertions ...") --
            # sweeping the whole line reports every explained dependency as a
            # self-dependency.
            deps[cur] = re.findall(r'F\d{3}', re.split(r'[—(]', m.group(1), maxsplit=1)[0])

sections = set(own)

# 1 — coverage table vs each feature's own line. The table is optional: the
# template never emitted one, so every template-shaped mission failed here.
if cov:
    for f in sorted(sections | set(cov)):
        if cov.get(f) != own.get(f):
            errs.append(f"F-list mismatch {f}: coverage table {cov.get(f)} vs its own section {own.get(f)}")
else:
    cov = dict(own)
    notes.append("no coverage table in features.md -- using each feature's own Assertions line")

# 1b — proof budget: every assertion says how hard it must be pinned (and no harder)
if cols.get("_budget") is None:
    notes.append("contract.md has no Proof budget column -- legacy contract, proof is unbounded")
else:
    for a in sorted(ct):
        if not ct_budget.get(a, "").strip("-— "):
            errs.append(f"{a} has no proof budget (min: <named test|mutation|pre-fix failure|playwright>; max: <n> pinning feature)")

# 1c — planning gate: a feature list finer than the files it touches is the
# decomposition that produced 56 commits over 23 files. Repair features (a
# `- **Repairs:**` line, written by the negotiate step) are left out on both
# sides: a repair re-touches the files its origin feature already lists -- that
# is what makes it a repair -- so counting it would trip the gate on the first
# validation round of every mission that found a defect.
if files:
    planned = {f for f in sections if f not in repairs}
    distinct = {x for f, fs in files.items() if f not in repairs for x in fs}
    if len(planned) > len(distinct):
        errs.append(f"feature count exceeds files touched: {len(planned)} features over {len(distinct)} distinct files -- merge features that share a file")
else:
    notes.append("no **Files:** lines in features.md -- feature/file gate not applied")

# 2 — contract -> features
for a, feats in ct.items():
    for f in feats:
        if f not in cov:
            errs.append(f"contract routes {a} -> {f}, which has no feature section")
        elif a not in cov[f]:
            errs.append(f"contract routes {a} -> {f}, but {f} lists {cov[f]}")

# 3 — features -> contract  (the direction the hand-written check was missing)
for f, asserts in cov.items():
    for a in asserts:
        if a not in ct:
            errs.append(f"{f} claims {a}, which is not in the contract")
        elif f not in ct[a]:
            errs.append(f"{f} claims {a}, but the contract routes {a} -> {ct[a]}")

# 4 — declared counts vs actual
m = re.search(r'\*\*Assertions:\*\*\s*(\d+)\s*[—-]\s*`structural`\s*(\d+).*?`interface`\s*(\d+).*?`conversational`\s*(\d+)',
              contract, re.S)
if not m:
    notes.append("no parsable coverage-count line in contract.md -- counts not verified")
else:
    want = dict(zip(("total", "structural", "interface", "conversational"), map(int, m.groups())))
    have = collections.Counter(ct_class.values())
    if want["total"] != len(ct):
        errs.append(f"contract declares {want['total']} assertions; {len(ct)} rows present")
    for cls in ("structural", "interface", "conversational"):
        if want[cls] != have.get(cls, 0):
            errs.append(f"contract declares {want[cls]} `{cls}`; {have.get(cls, 0)} rows carry it")

# 5 — dependencies name features that exist
for f, ds in deps.items():
    for d in ds:
        if d not in sections:
            errs.append(f"{f} depends on {d}, which has no feature section")
        if d == f:
            errs.append(f"{f} depends on itself")

# 5b — the reviewer seat in mission.md, if one is named, is a real seat
mission_md = read("mission.md")
if mission_md:
    m = re.search(r'(?im)^-?\s*\**Reviewer seat:\**\s*(.+)$', mission_md)
    if m:
        p = seat_problem(m.group(1), "mission.md's reviewer seat")
        if p: errs.append(p)

# 6 — dangling ids.
#
# An id absent from the contract table is fine ANYWHERE, provided the contract's
# own Amendments table declares it retired -- which step 4 requires regardless,
# because a numbering gap nobody explains reads as a corrupt file. Anything else
# is a genuine dangling reference: a typo, or half an edit.
#
# Scoping the allowance to a section instead was too narrow in practice. Real
# retirement records land in a Coverage note, a "Resolved:" section and a
# "Last updated" line, and all three are legitimate.
retired = set(re.findall(r'\bA\d{3}\b', amendments_text(contract))) - set(ct)
archived = [(f"archive/{p.name}", p.read_text()) for p in sorted((M / "archive").glob("*.md"))] if (M / "archive").exists() else []
for name, text in (("features.md", features), ("design.md", design), ("state.md", state), *archived):
    if text is None:
        continue
    seen = {a for a in re.findall(r'\bA\d{3}\b', text) if a not in ct}
    for a in sorted(seen - retired):
        errs.append(f"{name} references {a}, which is neither in the contract nor "
                    f"declared retired in contract.md's Amendments table")
    if seen & retired:
        notes.append(f"{name}: {', '.join(sorted(seen & retired))} — retired, declared in "
                     f"contract.md, referenced here as a record. Expected.")

# 7 — design guideline ids resolve
if design is not None:
    defined = set(re.findall(r'^\|\s*(D\d+[a-z]?)\s*\|', design, re.M))
    for d in sorted(set(re.findall(r'\bD\d{3}[a-z]?\b', design))):
        if d not in defined:
            errs.append(f"design.md references {d}, which is not defined in its guideline table")
    for f in sorted(set(re.findall(r'^###\s+(F\d{3})\b', design, re.M))):
        if f not in sections:
            errs.append(f"design.md has a per-feature section for {f}, which features.md does not define")

# 8 — follow-up registry: every finding has a root-cause cluster and a
# disposition, and a cluster is repaired by ONE feature. Eighty-two follow-ups
# with no severity, cluster or disposition became eighty-two features.
followups = read("followups.md")
if followups:
    entries = re.split(r'(?m)^##\s+(FU\d{3})\b', followups)[1:]
    entries = list(zip(entries[0::2], entries[1::2]))
    has_registry = any(re.search(r'\*\*Cluster:\*\*', body) for _, body in entries)
    if entries and not has_registry:
        notes.append("followups.md has no **Cluster:** fields -- legacy queue, registry checks skipped")
    elif entries:
        cluster_fix = collections.defaultdict(set)
        for fu, body in entries:
            c = re.search(r'\*\*Cluster:\*\*\s*(\S+)', body)
            d = re.search(r'\*\*Disposition:\*\*\s*(.+)', body)
            if not c: errs.append(f"{fu} unclustered: add **Cluster:** <root-cause id>")
            if not d: errs.append(f"{fu} has no **Disposition:** (repair as F00n | accept | waived by <who>)")
            if c and d:
                r = re.search(r'repair as (F\d{3})', d.group(1))
                if r: cluster_fix[c.group(1)].add(r.group(1))
        for c, fs in sorted(cluster_fix.items()):
            if len(fs) > 1:
                errs.append(f"cluster split across features: {c} is repaired by {', '.join(sorted(fs))} -- one cluster, one repair feature")

# ---- report -----------------------------------------------------------------
print(f"  assertions {len(ct)} · features {len(cov)} · "
      f"classes {dict(collections.Counter(ct_class.values()))}")
for n in notes:
    print(f"  note: {n}")
if errs:
    print("\nCHECK FAIL: the mission directory disagrees with itself.\n")
    for e in errs:
        print(f"    {e}")
    print("\n  An amendment is not done while any line above is true. Fix, re-run.")
    sys.exit(1)
print("\nCHECK PASS: coverage consistent in both directions, no dangling ids, dependencies resolve.")
PY
