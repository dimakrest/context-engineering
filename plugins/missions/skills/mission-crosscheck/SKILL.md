---
name: mission-crosscheck
description: The cross-vendor blind review of a mission's plan, run after /missions:mission-spec or after /missions:mission-design. Seals a spec package with our conclusions stripped out, has an external reviewer derive the architecture independently, audits the transcript for contamination before a single finding is read, then routes contract defects to the user and design findings to design.md. Never patches contract.md or features.md. Writes zero product code. Use when the user says "/missions:mission-crosscheck", asks for an outside or unbiased opinion on a mission's contract or design, or wants the plan checked before /missions:mission-run.
---

Read [the runtime guide](../../docs/RUNTIMES.md) before following this workflow.

# /missions:mission-crosscheck — an outside opinion that is actually outside

Claude-authored missions receive Codex reviews; Codex-authored missions receive Claude reviews.
Declare the actual author provider explicitly. The shared `crosscheck.py` helper selects the opposite
provider and rejects same-vendor or unknown configurations before dispatch. It is independent of
the implementation driver and spends vendor independence on the plan before code exists to defend.

**The failure mode this skill exists to prevent is not a bad review. It is a contaminated one.** A
reviewer that has seen our conclusions produces a fluent, well-cited report that agrees with us, and
nothing about reading it reveals that it is an echo. The first real run failed exactly this way. So
the sealing is mechanical, not a matter of prompting care, and the audit in Step 4 is a gate you may
not skip because the output looks fine. It always looks fine.

**You write no product code here.** Phase stays `planning`.

## Preconditions

- `contract.md`, `mission.md` and `features.md` exist. In `design` mode, `design.md` too.
- The opposite provider CLI is installed and authenticated. Run the shared preflight below.
  Failure is visible and blocking: never fall back to another provider or substitute a subagent.
- Phase is `planning`. If it is not, this is the wrong moment: after implementation starts, findings
  cost a rewrite instead of an edit.

## Modes — and the earlier one matters more

| Mode | When | Asks |
|---|---|---|
| `contract` (default) | after `/missions:mission-spec`, before `/missions:mission-design` | attack the contract; what would you refuse to build |
| `design` | after `/missions:mission-design` | the full independent architecture, plus an optional sighted divergence pass |

Run `contract` mode even if you intend to run `design` mode later. A wrong assertion is the one
thing the rest of the machinery cannot recover from, and it is cheapest to find before a design pass
has been built on top of it. The first real run found an assertion that described a surface which
does not exist — a defect that needed no design to see, and that had already propagated into
`features.md` by the time it was caught.

## Progress — verify evidence before skipping work

`crosscheck/progress.json` is the machine record; `crosscheck/progress.md` remains the findings and
disposition record. Read both at entry. A Markdown checkbox is not proof of completion. Run the
same helper command to resume: it verifies author/reviewer, executable hash, model, mode, mission,
package/task hashes, process receipt, transcript, snapshot and saved report before reusing a pass.
A completed but unaudited process is audited without another reviewer call. An unchanged valid
pass is reused, even if authentication is no longer available. Legacy progress without this
evidence cannot establish completion.

Incomplete, contaminated, changed or unauditable output becomes `VOID-*` outside the repository.
Fix the cause, reseal changed inputs, then explicitly use `--new-pass`; never automatically retry a
failed review or salvage findings. If the saved process is alive, wait for it before resuming.
The helper records `blind` and `sighted` independently, including their identities, evidence paths,
process outcome, audit status/reason and report hash. Sighted work never satisfies the blind gate.

```markdown
# Crosscheck findings

Machine evidence: progress.json. Checkboxes cannot establish completion.

| # | Finding | Bucket | Verified | Disposition |
|---|---|---|---|---|
```

The package, raw stdout/stderr and snapshots live outside the repository. Do not tail reviewer
output to check progress: use file size/process status until the audit passes.

## Reviewer preflight — no mission writes

Resolve the plugin path as described in the runtime guide. Set `AUTHOR` to `claude` or `codex`
according to who actually authored the mission, and `MODE` to `contract` (default) or `design`.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/mission-crosscheck/crosscheck.py" preflight \
  --author "$AUTHOR" --mission "$MISSION" --mode "$MODE"
```

Preflight checks the selected executable's version, required flags and authentication using
bounded probes (15 seconds each; `--preflight-timeout` can set at most 60 seconds). It makes no
model call and writes no mission artifact. Only trusted native Claude and Codex installations
are supported. `--reviewer` may name only the other provider; `--executable` selects a verified
installation of that provider; `--model` accepts only that provider's model names. Defaults are
Claude `opus` and Codex `gpt-5.4`. Custom gateways, unknown routing, arbitrary extra arguments and
fallback providers are unsupported. CLI/version identity is a check of a trusted local binary,
not cryptographic proof against an executable deliberately impersonating a vendor.

## Step 1 — seal the package, outside the repo

Choose a dedicated absolute scratch path outside the repository. The helper also rejects paths
reachable through repository symlinks. The package contains only renamed inputs, `TASK.md`,
`leak-hits.json` and `SEAL.json`; raw output belongs in a separate external run directory.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/mission-crosscheck/crosscheck.py" seal \
  --mission "$MISSION" --mode "$MODE" --package "$PKG" \
  --leak-pattern "<terms specific to this mission's conclusions>"
```

The helper copies `contract.md` → `SPEC-1-contract.md`, `mission.md` → `SPEC-2-scope.md`, and
`features.md` → `SPEC-3-decomposition.md`. It removes Procedures blocks and Seat lines from
features, the Reviewer seat from mission, and Amendments sections. It retains ids, assertions,
dependencies, scope and non-goals. It never includes `design.md` in the blind package.

It checks for `paginat|twin|parity|D0[0-9][0-9]` plus the supplied mission-specific pattern. Inspect
`leak-hits.json`: report and assess each hit, never silently edit specification text to hide it.
A benign hit, such as an original non-goal, can be retained with an explicit reason. Supply an
external JSON assessment keyed by hit id and rerun `seal --leak-assessment <absolute-file>`:

```json
{"1":{"disposition":"keep","reason":"Original non-goal predates the design and discloses no conclusion."}}
```

An actual leak blocks sealing; correct the source of the leak with the user's authority before
resealing. The manifest pins source, package and task hashes. Dispatch and audit both verify it.
Never add transcripts or other artifacts to a sealed package.

## Step 2 — write the task file

The helper writes `TASK.md` into the package directory and the seal pins its hash; dispatch and
audit both re-derive it, so it cannot drift or be edited. Inspect it before dispatch. It contains:

1. **Absolute paths to the three inputs.** Never "the files in this directory" — the reviewer's
   working directory is a fresh external directory. The task also names the absolute repository path and safe search roots.
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

## Step 3 — run or resume the reviewer

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/mission-crosscheck/crosscheck.py" run \
  --author "$AUTHOR" --mission "$MISSION" --mode "$MODE" --package "$PKG"
```

Run with a long host timeout and keep the process alive. Default reviewer timeout is 3600 seconds;
`--timeout` can change it. Several minutes without output can be normal. Check file sizes and
process status, and wait for the exit; never inspect raw findings before the audit.

Both providers start fresh in an external working directory with explicit absolute task paths.
Claude uses `--output-format stream-json --verbose`, `--safe-mode`, empty setting sources,
disabled hooks, strict empty MCP config, `--no-session-persistence`, and only native `Read,Grep,Glob`
tools. `--safe-mode` disables automatic project context while retaining normal authentication.
Flags are capability-checked before launch. See the
[Claude CLI reference](https://code.claude.com/docs/en/cli-reference).
Codex uses JSONL, `--sandbox read-only`, `--ephemeral`, `--ignore-user-config`, an explicit OpenAI
provider/model and disabled project instruction loading. See the
[Codex CLI reference](https://developers.openai.com/codex/cli/reference/).

Before dispatch the helper hashes repository contents, including ignored and already-dirty files,
and captures Git HEAD, branch and index plus mission checksums. Only the workflow's exact
`crosscheck/{progress.json,progress.md,pass1-report.md,pass2-report.md,report.html}` files are excluded.
Other `crosscheck/` files and mission `VOID*` files remain protected. The existing shell entrypoints
stay compatible: `snapshot.sh <mission> <external-snapshot>` and `snapshot.sh --print <mission>`.

## Step 4 — the audit gate

The helper captures process exit separately from stdout/stderr, then audits before saving any
report. It requires a valid baseline, unchanged repository content, unchanged package/task inputs,
a valid structured stream, consistent session identity, resolved tool calls, successful terminal
result, nonempty report and process exit zero. Unknown events or tools that cannot be audited
void the pass. Reads, discovery, outputs and citations into `.missions/` or `docs/plans/`, including
absolute paths and resolved aliases, are contamination. Real exclusion operands and task warnings
are distinguished from accesses. Shell commands are a restricted literal read/search subset;
opaque scripts or expansion are unauditable. Access is limited to repository source, the sealed
package and the explicitly designated external inputs for that pass; prior external review evidence
is not an allowed input. Claude native searches use explicit safe subtrees.

`audit.sh <transcript> <mission> <snapshot>` remains available for saved helper runs. Codex reviews
now use structured JSONL transcripts: a text terminal marker can be forged by tool output and
cannot establish completion. Legacy text/progress without structured process, seal and snapshot
evidence must be rerun; use `--new-pass` to replace legacy machine progress.

On success the helper saves `crosscheck/pass1-report.md` and its hash. Only now read findings.
On failure it quarantines output as `VOID-*`, records the reason and exits nonzero. Do not salvage
any section. Fix the cause before using `--new-pass`.

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

Only after the audited blind report is saved:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/mission-crosscheck/crosscheck.py" run \
  --author "$AUTHOR" --mission "$MISSION" --mode design --package "$PKG" --sighted
```

The helper revalidates the blind pass, then gives a fresh reviewer external copies of that report
and `design.md`. Ask for divergences: question at stake, each design's choice, which is right and
what observation settles it; shared assumptions; and what each design lacks. Do not defer, concede
because we wrote more, or manufacture divergences. Other mission documents remain out of bounds.

Blindness ends deliberately for this separate pass. Its task, inputs, process, audit and report
(`pass2-report.md`) are recorded under `sighted`; it cannot replace `blind` or its saved report.

## Step 7 — hand over

Write `crosscheck/report.html`, record assessment/report completion in `progress.md`, and journal a
`decision`. Do not change machine audit results or treat a sighted pass as blind completion. The
journal write is a new mission input state: subsequent runs revalidate and may require a fresh pass. Report
tightly: the convergences first (they are the cheapest thing the user can act on — stop worrying
about that decision), then the defects ranked by cost, then what needs the user's hand and why you
did not do it yourself.

Then stop. Amending `contract.md` is the user's; `/missions:mission-design` or `/missions:mission-run` is a separate,
explicit invocation.
