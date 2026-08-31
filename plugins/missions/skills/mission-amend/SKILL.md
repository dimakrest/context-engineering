---
name: mission-amend
description: Change a planned mission's contract, decomposition or scope after /missions:mission-plan and /missions:mission-design have run, without leaving half of it behind. Maps the blast radius first, applies edits that abort rather than half-apply, retires ids without renumbering, sweeps to zero live references, and gates on a bidirectional coherence check. Planning phase only. Use when the user says "/missions:mission-amend", wants to cut or widen a mission's scope, or acts on a contract defect found by /missions:mission-design or /missions:mission-crosscheck.
user_invocable: true
---

# /missions:mission-amend — change the plan without leaving half of it behind

Every other mission skill assumes the plan is right. This one exists for the moment it is not — a
contract defect a reviewer found, a scope the user has narrowed, a design decision that reversed
under evidence. The framework's only answer to that today is the halt trigger, which stops the loop
and hands you a decision. This skill is what happens after you make it.

**The failure mode is not a wrong edit. It is a partial one.** A feature deleted from its own section
but still named in a `Depends on:` line, an assertion a feature claims that the contract routes
elsewhere, a retired id nobody explained. Each file still reads correctly on its own — that is
exactly why nobody notices — and the first thing to find it is a worker at 2am building the feature
you thought you deleted. A half-removed feature is worse than either removing it or keeping it.

**You write no product code here.** Phase stays `planning`.

## Preconditions

Read `state.md` first. **The phase must be `planning`.** If it is anything else, stop and say so:
after implementation starts, an amendment can invalidate a `proven` assertion, orphan a committed
feature, and strand a handoff that described the old contract. This skill does not handle that, and
guessing at it is worse than refusing. Tell the user to halt the loop (`/missions:mission-run`'s halt
triggers) and decide deliberately — the amendment may still be right, but it is no longer cheap.

Then read `mission.md`, `contract.md`, `features.md`, `design.md` and `followups.md`. You are about
to change how they relate to each other; read all of them, not the one you think you are editing.

## Step 1 — is this an amendment, or a re-plan?

Force this decision before touching anything. The pull is always toward editing, because editing
feels smaller.

**Amend** when the defect is localized and you can name every site. **Re-plan** when it is systemic —
when you cannot say what is wrong, only that the contract keeps being wrong.

Three questions that settle it:

- **Is anything built?** In `planning`, no. The usual reason to prefer a rewrite — accumulated
  implementation debt — does not exist, and "update" means editing markdown.
- **Does the decomposition survive?** If deleting one feature leaves a milestone that no longer means
  anything, the structure was load-bearing on the thing you are removing. Re-plan.
- **Is the surviving contract better tested than a fresh one?** After a `/missions:mission-crosscheck` pass it
  usually is. A fresh contract lands unaudited — you would be trading known defects for unknown ones,
  and re-deriving the assertions that are fine.

A high defect *count* is not itself an argument for re-planning. Defects you have found and located
are the cheap kind. Write the answer into the amendment record either way; if re-plan wins, stop and
say so rather than doing a large amendment quietly.

## Step 2 — map the blast radius before you edit anything

Grep the **whole mission directory** for every id, feature name and phrase the amendment touches, and
produce the site list first:

```bash
grep -rnE "F004|A015|<the phrases this amendment retires>" "$MISSION"
```

Read the list before writing a byte. A real scope cut ran to fifteen sites across five files, and two
of them were in files nobody would have thought to open — a guideline's applicability column, and a
single summary sentence in `state.md`. Editing first and searching after is how the residue survives.

Include `docs/plans/<slug>-plan.*` in the sweep. It is not mission state, but it is the human-facing
document, and a plan doc that contradicts the contract is worse than no plan doc.

## Step 3 — apply edits that abort rather than half-apply

Every replacement **asserts it matched exactly once** before anything is written, and one missed
anchor aborts the entire edit:

```python
def sub1(old, new):
    global s
    assert s.count(old) == 1, f"count={s.count(old)}: {old[:70]!r}"
    s = s.replace(old, new)
```

Write the whole file at the end, never incrementally. This is not ceremony: a real amendment hit two
bad anchors, and because nothing had been written yet, the fix was to correct the anchor and re-run.
A best-effort `sed` in the same position changes nothing, reports success, and leaves you believing
an edit landed.

**Keep the file contents in exactly one binding.** If some edits go through `sub1` and others
reassign a *different* variable holding the same text, one set silently wins and the other is
discarded — and every assertion still passes, because each edit really did apply, just to a copy
nobody wrote out. Thread one value through a holder:

```python
box = [path.read_text()]
def sub1(old, new):
    assert box[0].count(old) == 1, f"count={box[0].count(old)}: {old[:60]!r}"
    box[0] = box[0].replace(old, new)
# ... every mutation goes through box[0] ...
path.write_text(box[0])
```

This is not hypothetical: a test run of this very skill lost four replacements that way, kept the
section deletions, and reported all four files amended. Step 5's sweep is what caught it — which is
the argument for never skipping the sweep because the edits "obviously worked".

Prefer anchoring on a row's own distinctive tail over its leading id — table rows share prefixes and
differ at the end.

## Step 4 — deletion rules

- **Ids are never renumbered and never reused.** A retired assertion leaves a gap in the numbering,
  and that is correct.
- **Declare the retirement in `contract.md`'s own `Amendments` table.** This is not bookkeeping:
  without it the gap reads as a corrupt file, and a blind reviewer will report the missing id as a
  defect. `check.sh` uses that declaration to tell a retired id from a dangling one.
- **A deleted feature must be chased through five places**, not one: its own section, the coverage
  table, every `Depends on:` line naming it, every guideline's applicability column in `design.md`,
  and its per-feature section in `design.md`.
- **Leave a guard where a worker would re-add it.** When a surface leaves the mission, an
  anti-pattern in `design.md` saying so in as many words is what stops someone adding it back "while
  in there". The contract asserting its *absence* is stronger still.

## Step 5 — sweep to zero

Re-run Step 2's grep. Every surviving hit must be **deliberate record text**, and you name each one
rather than counting them. A hit you cannot explain is an unfinished edit.

**Sweep the whole directory, not the five canonical files.** A real amendment left a 624KB quarantined
review transcript sitting inside the mission directory holding a verbatim copy of the *pre-amendment*
`contract.md` and `features.md` — so a grep of the mission dir surfaced the retired assertion and the
deleted feature as if they were live, and any agent reading that directory would have found the old
plan intact. Move any such artifact out of the mission directory. Raw review transcripts belong
outside it regardless; `/missions:mission-crosscheck` says so.

## Step 6 — the coherence gate

Run it verbatim. It exits non-zero if the files disagree with each other:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/check.sh "$MISSION"
```

It checks coverage in **both directions** — contract → features and features → contract — plus each
feature's own assertion line against the coverage table, the declared class counts against the actual
rows, dependencies that resolve, guideline ids that resolve, and ids that are neither in the contract
nor declared retired.

Both directions matter. A hand-written one-directional version of this check passed a mission whose
`A002` was claimed by F001 while the contract routed it to F002 alone — every assertion had a home,
every feature had assertions, and the two files still disagreed.

Do not report an amendment as done while this fails.

## Step 7 — record what moved and why

An `Amendments` row in **every file you changed**, and a journal event. The row says what moved and
why, with `file:line` evidence wherever a fact about the source drove the change — "A015 retired"
is bookkeeping; "A015 retired: it asserted a comparison between two periods, but the endpoint takes
one date range and compares escalation cohorts (`analytics_service.py:213-215`)" is a reason the next
reader can check.

```jsonl
{"ts":"...","event":"amendment","step":"<what prompted it>","files":["contract.md","features.md"],"summary":"...","contract_changed":true}
```

`contract_changed` is not decoration — Step 8 branches on it.

## Step 8 — hand over

**If `contract.md` changed, the amendment is not complete.** Run `/missions:mission-crosscheck contract` and
report only after its audit passes.

This is the step people will want to skip, so here is the evidence: a careful amendment pass rewrote
seven assertions and the blind review that followed found **three fresh defects in those seven** — a
filter silently dropped while making a list explicit, a monotonicity claim that is false for
averages, and an exclusion list naming three of five tabs. Every one was introduced by the pass that
was fixing defects. An amendment checked only by the person who wrote it is worth about as much as a
worker grading its own diff, and the whole framework is built on not doing that.

If only `design.md` or `features.md` changed, a crosscheck is recommended but not required — those
files are the design's to fix, and `/missions:mission-design` will read them again anyway.

Then report: what changed, what you deliberately did **not** change and why, the sites the sweep
found, and anything the amendment revealed that the user still owes a decision on.

Then stop. `/missions:mission-run` is a separate, explicit invocation by the user.
