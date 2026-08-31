---
name: mission-crosscheck
description: The cross-vendor blind review of a mission's plan, run after /missions:mission-plan or after /missions:mission-design. Seals a spec package with our conclusions stripped out, has an external reviewer derive the architecture independently, audits the transcript for contamination before a single finding is read, then routes contract defects to the user and design findings to design.md. Never patches contract.md or features.md. Writes zero product code. Use when the user says "/missions:mission-crosscheck", asks for an outside or unbiased opinion on a mission's contract or design, or wants the plan checked before /missions:mission-run.
user_invocable: true
---

# /missions:mission-crosscheck — an outside opinion that is actually outside

Every check a mission runs is a Claude checking Claude. `MISSIONS.md` names this as the
framework's known weakness: we can vary model, context blindness and evidence independence, but not
vendor. This skill supplies the missing axis, and spends it on the artifact that most deserves it —
the plan, before the code exists to be defended.

**The failure mode this skill exists to prevent is not a bad review. It is a contaminated one.** A
reviewer that has seen our conclusions produces a fluent, well-cited report that agrees with us, and
nothing about reading it reveals that it is an echo. The first real run failed exactly this way. So
the sealing is mechanical, not a matter of prompting care, and the audit in Step 4 is a gate you may
not skip because the output looks fine. It always looks fine.

**You write no product code here.** Phase stays `planning`.

## Preconditions

- `contract.md`, `mission.md` and `features.md` exist. In `design` mode, `design.md` too.
- `codex` is on PATH (`codex --version`). If not, say so and stop; do not substitute a Claude agent
  and call it a crosscheck — the whole point is the vendor boundary.
- Phase is `planning`. If it is not, this is the wrong moment: after implementation starts, findings
  cost a rewrite instead of an edit.

## Modes — and the earlier one matters more

| Mode | When | Asks |
|---|---|---|
| `contract` (default) | after `/missions:mission-plan`, before `/missions:mission-design` | attack the contract; what would you refuse to build |
| `design` | after `/missions:mission-design` | the full independent architecture, plus an optional sighted divergence pass |

Run `contract` mode even if you intend to run `design` mode later. A wrong assertion is the one
thing the rest of the machinery cannot recover from, and it is cheapest to find before a design pass
has been built on top of it. The first real run found an assertion that described a surface which
does not exist — a defect that needed no design to see, and that had already propagated into
`features.md` by the time it was caught.

## Progress file — this skill is re-entrant

Maintain `.missions/<slug>/crosscheck/progress.md`. Read it first, every time; skip any step it
records as done. A reviewer pass takes ~10 minutes and real tokens, and a session can die mid-run.

```markdown
# Crosscheck — <slug>

**Mode:** contract | design
**Package:** <absolute path, outside the repo>
- [x] 1 package sealed — 3 files, leak-check: 1 benign hit (reported, kept)
- [x] 2 task file written — TASK.md
- [x] 3 reviewer run — pass1.raw.md, 220,659 tokens
- [ ] 4 audit — PASS | VOID (reason)
- [ ] 5 findings assessed
- [ ] 6 pass 2 divergence — design mode only, or "skipped"
- [ ] 7 report — crosscheck/report.html

## Findings
| # | Finding | Bucket | Verified | Disposition |
|---|---|---|---|---|
```

The raw transcript and the sealed package live **outside** the mission directory. A transcript is
~750KB and is not run state, and the package must never be reachable from inside the repo.

## Step 1 — seal the package, outside the repo

Stage in the session scratchpad — never under `.missions/`, never anywhere in the project. Copy the
inputs under **different names**, so a filename search cannot land on the originals:

| Source | Becomes |
|---|---|
| `contract.md` | `SPEC-1-contract.md` |
| `mission.md` | `SPEC-2-scope.md` |
| `features.md`, procedures stripped | `SPEC-3-decomposition.md` |

Strip `features.md`'s per-feature `Procedures` blocks — that is where the reasoning lives — and keep
the ids, assertions, dependencies and scope boundaries, so the decomposition can still be critiqued:

```python
re.sub(r'- \*\*Procedures:\*\*\n(?:(?:  - |    ).*\n|\n(?=  ))*?(?=- \*\*Depends on:\*\*)', '', s)
re.sub(r'- \*\*Seat:\*\*.*\n', '', s)          # our judgment of which features are hard
```

Strip the `Reviewer seat:` line from `mission.md` for the same reason. Also drop any Amendments
section. Then leak-check the package and **report hits rather than editing
them**:

```bash
grep -rniE "paginat|twin|parity|D0[0-9][0-9]|<terms specific to this mission's conclusions>" "$PKG"
```

A hit is not automatically a leak. In the first run the only hit was a non-goal in `mission.md`
("no query rewrite") that predated the design pass and said nothing about our answer — removing it
would have been tampering with the specification. Surface hits, judge each, and say which you kept.

**Why outside the repo, specifically.** Run 1 staged the package inside `.missions/` and told the
reviewer `.missions/` was out of bounds. Its first search honoured that exclusion — which also hid
the package — found nothing, and it then enumerated `.missions/` by filename and read the unstripped
original. The blacklist ate the whitelist. `mission-crosscheck-seal.sh` now blocks a `codex`
invocation that references `.missions/` or `docs/plans/`, but that hook only guards the command line;
the seal has to be right by construction.

## Step 2 — write the task file

Write `TASK.md` into the package directory. It must contain, and these are not optional:

1. **Absolute paths to the three inputs.** Never "the files in this directory" — the reviewer's
   working directory is the repo root, not the package.
2. **A named-decoy warning.** State plainly that longer, annotated copies of the same documents
   exist under `.missions/` and `docs/plans/`, that both trees are out of bounds entirely, and that
   a filename search must not be followed there. Run 2 carried the exclusions in its own search
   commands after being told this; run 1, told only "out of bounds", went looking.
3. **The framing: derive, do not review.** "Another team has independently produced their own
   architecture for the same specification; you have not seen theirs and they have not seen yours.
   The point is divergence." Never show it our answer and ask what it thinks — that anchors it onto
   our conclusion and it will argue inside our frame.
4. **Read-only ground rules.** No writes, no branch, no commit, no database connection.
5. **The task list**, by mode. `contract` mode: attack the contract (is each assertion observable?
   provable at its stated class? *could an implementation satisfy it literally and still be wrong?*
   what behaviour does no assertion cover?) and what would you refuse to build. `design` mode adds:
   the forced decisions — you decide which they are — with options, your choice, and **what would
   have to be true for your choice to be wrong**; plus whether the decomposition carries it.
6. **The output contract.** Markdown, one section per task, `file:line` for every claim about the
   repo, and every substantive claim tagged `[verified: <citation>]`, `[inferred]` or `[uncertain]`.
   Add both guards: *"do not manufacture findings to appear thorough — 'no issues found in this
   section' is a legitimate answer"* and *"do not hedge into uselessness; commit to a choice."*

## Step 3 — run the reviewer

Snapshot first, so Step 4 can prove nothing was written — the reviewer's sandbox defaults to
`workspace-write` regardless of what the prompt says:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/skills/mission-crosscheck/snapshot.sh "$MISSION" "$SNAP"
```

That script is the **only** definition of what counts as a mission file; Step 4 recomputes through
the same script rather than a second copy of the expression. It skips `VOID*` transcripts and this
skill's own `crosscheck/` directory, so **it does not matter whether you write the progress file
before or after the snapshot** — it is excluded either way. Do not hand-roll the `find`: two copies
of it is how the gate ends up accusing an innocent run of writing its own progress file.

Then run it in the background, with a long timeout:

```bash
codex exec --cd "$REPO" < "$PKG/TASK.md" > "$PKG/pass1.raw.md" 2>&1
```

Expect ~10 minutes and ~200k tokens. **A three-to-four minute gap with no output is normal, not a
stall** — `reasoning summaries: none` means thinking produces nothing on stdout. Do not kill it.
Check progress by file size and by tailing the transcript.

## Step 4 — the audit gate

**Run this before reading a single finding.** Not after skimming the report, not "if something looks
off" — the whole problem is that a contaminated report looks right.

```bash
bash ${CLAUDE_PLUGIN_ROOT}/skills/mission-crosscheck/audit.sh "$PKG/pass1.raw.md" "$MISSION" "$SNAP"
```

It fails on any of: a read of or citation into a sealed path, a change to `git status`, a change to
any mission file's checksum, or a missing terminal marker (an unfinished run).

On failure the run is **void**. Quarantine the transcript as `VOID-<date>.raw.md`, record the reason
in the progress file, fix the seal, and re-run. Do not salvage a contaminated pass — not partially,
not for one section that "looks unaffected". Its value was independence, and that is gone.

Then extract the report: the last `codex` block before the terminal `tokens used` line.

## Step 5 — assess the findings

Sort every finding into one bucket, and **spot-verify against source** anything in the first two
before it reaches the user. The reviewer tags its own confidence; treat `[inferred]` as a lead, not
a fact.

| Bucket | Disposition |
|---|---|
| **Contract defect** | Report to the user. **Never patch `contract.md` from here.** The user owns the contract — the same rule `/missions:mission-design` obeys. Once they have decided, `/missions:mission-amend` applies it and comes back through this skill. |
| **Design or decomposition finding** | Propose the edit to `design.md` / `features.md`; apply only on the user's word. |
| **Scope proposal** | Default to rejecting it against `mission.md`'s non-goals, and name it so the user can overrule. An outside reviewer has no stake in your scope and will propose adjacent refactors in good faith. |
| **Convergence with our design** | Record it. Independent derivation of a decision we agonised over is a result, not a null finding — it is what lets the user stop relitigating it. |

Rank the defects by cost of being wrong, not by how interesting they are.

## Step 6 — pass 2, divergence (design mode, optional)

Only after pass 1's report is on disk. Give the reviewer its own output and our `design.md`, and ask
for: the divergences (question at stake, what each design does, which is right, and what observation
would settle it); where both may be wrong through shared assumption rather than shared evidence; and
what each has that the other lacks. Tell it not to defer, not to concede because we wrote more, and
not to manufacture divergences.

This is where blindness ends by design. `design.md` is now in its context, so the pass cannot be
repeated — capture pass 1 first or the artifact is lost.

## Step 7 — hand over

Write `crosscheck/report.html`, mark the progress file complete, and journal a `decision`. Report
tightly: the convergences first (they are the cheapest thing the user can act on — stop worrying
about that decision), then the defects ranked by cost, then what needs the user's hand and why you
did not do it yourself.

Then stop. Amending `contract.md` is the user's; `/missions:mission-design` or `/missions:mission-run` is a separate,
explicit invocation.
