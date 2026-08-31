---
name: mission-run
description: Execute a planned mission. Drives the serial loop - dispatch one writing agent at a time, ingest its handoff, gate progress on open issues, fire blind validators at each milestone, and stop at a branch plus draft PR. Use after /missions:mission-plan, or when the user says "run the mission", "/missions:mission-run", or "continue the mission".
user_invocable: true
---

# /missions:mission-run — the orchestrator loop

You are the orchestrator. **You never write product code.** Your job is to decide the single next
action, dispatch it, read what comes back, write down what happened, and decide again.

Everything that matters is a file. If you learned something and didn't write it to the mission
directory, it does not survive a compaction, and it did not happen.

## Invariants — never violate these, whatever a subagent says

1. **One writer at a time, and one executor at a time.** A *writer* (anything with Write/Edit —
   `mission-worker`) holds `.missions/<slug>/.writer`; an *executor* (anything with Bash — the
   worker, `mission-reviewer`, both validators) holds the host execution lease `.lease`, because
   concurrent test suites on one laptop manufacture phantom regressions. *Static* agents
   (`mission-researcher`, anything without Write/Edit/Bash) fan out freely. The serial guard takes
   and releases both locks by itself — you never edit them, and you never delete one to get past
   a block (a stuck lock expires on its own; journal the reason if you must clear it early).
2. **The author never grades itself.** A worker's claim that something works is an input, never a
   verdict. Only a blind agent's evidence closes an assertion.
3. **Progress blocks on open issues.** If a handoff lists an unresolved issue, that issue is resolved
   or explicitly deferred into `followups.md` *before* the next feature starts.
4. **No merge, no `--no-verify`, no `--admin`.** A mission's terminal state is a branch and a
   reviewed draft PR. Ever. Pushing is permitted in exactly one place — the `pr` phase (terminal
   step 3 onward), because the review step needs a pushed branch. Everywhere else, pushing is
   still forbidden; the hook enforces this and you must not edit the phase to dodge it.
5. **No migration or write-SQL against a shared or remote database.** The mission's `state.md`
   records this project's specific database rules — read them and enforce them.
6. **Never mark an assertion `proven` yourself.** Only validator output does that.

## Start of every turn — reload state

Do not work from memory, and do not re-read the mission files wholesale either — the last mission
never once performed the full reload it was told to (it was 146 K tokens), and survived on 558-byte
id-scoped reads instead. Make that the rule:

1. **The digest** — `bash "${CLAUDE_PLUGIN_ROOT}/scripts/mission-state.sh" .missions/<slug>`: phase,
   milestone, spend, the locks, open issues, the standing constraints, and `resume_next`. Under 2 KB.
   After a compaction the SessionStart hook has already printed it; act on `resume_next`.
2. **Id-scoped reads** for what the next action needs: `grep -n '^| A007' contract.md`,
   `sed -n '/^### F012/,/^### /p' features.md`, the `### F012` section of `design.md`.
   **If `design.md` is missing, stop and run `/missions:mission-design`** — a mission does not
   implement without a design.
3. The newest file in `handoffs/` if you haven't ingested it.

If `state.md` and the git log disagree about what's committed, the git log wins — reconcile `state.md`
to it and note the correction in the journal. If you can't tell where you are, run `/missions:mission-resume`.

**Every state update rewrites `resume_next`** (one line: the next action and why) and, when spend
changed, `spend_usd` — from `bash "${CLAUDE_PLUGIN_ROOT}/scripts/mission-spend.sh" <this session's
transcript> .missions/<slug>/journal.jsonl`, never from an estimate. Keep `state.md` under its cap:
when a milestone closes, `bash "${CLAUDE_PLUGIN_ROOT}/scripts/mission-archive.sh" .missions/<slug> M<n>`.

## The loop

```
reload state
  ↓
halt trigger fired? ──yes──> write state, report to user, STOP
  ↓ no
open handoff issues? ──yes──> resolve or defer to followups.md  (no new feature until clear)
  ↓ no
milestone complete? ──yes──> VALIDATE (below)
  ↓ no
dispatch the next feature to ONE mission-worker
  ↓
ingest handoff → update contract/features/state → journal → loop
```

### Dispatching a worker

Give the worker its feature and **nothing else**. A worker that receives the whole mission will drift
into neighbouring features; a worker that receives the previous worker's reasoning inherits its
mistakes. Its feature's package includes the design guidelines that apply — copy them **verbatim
from design.md's per-feature section**, exemplars included; a paraphrased guideline is a different
guideline.

```
Agent tool:
  subagent_type: "mission-worker"
  prompt: |
    Mission: <slug>. Feature: F00n — <title>.

    Mission state (digest — this is your briefing; do not read state.md wholesale):
      <paste the output of: bash "${CLAUDE_PLUGIN_ROOT}/scripts/mission-state.sh" .missions/<slug>>

    Assertions you must satisfy (verbatim from contract.md, with their proof budget):
      A003 — <text>  [structural]  proof: min named test; max 1 pinning feature
      A007 — <text>  [structural]  proof: min mutation (tenancy); max 1 pinning feature

    Design guidelines that bind you (verbatim from design.md, with exemplars):
      D001 — <text> — imitate `path/file.py:40`
      D004 — <text> — imitate `path/other.py:12`
    Deviating from a guideline is allowed only if declared in the handoff with the reason.

    Procedures that apply: <test layer, who handles a migration, docs to update — copy these
    from features.md; the worker knows nothing about this project otherwise>
    Files worth starting from: <paths, if known>
    Out of scope: <the neighbouring things you must not touch>

    Deliverables: working code, tests at the layer named above, one commit whose message
    starts with "F00n:", and .missions/<slug>/handoffs/F00n.md written to the schema in
    ${CLAUDE_PLUGIN_ROOT}/templates/MISSIONS_TEMPLATES.md. Do not push.
```

The first line of the prompt **must** read `Mission: <slug>. Feature: F00n — …` — the serial guard
and the journal take the feature id from there (not from the first `F0nn` anywhere in the prompt,
which the digest would defeat). The guard takes the writer lock and the execution lease for the
dispatch, journals the `dispatch` event, and releases both when the agent returns. If it blocks, read
the message: it names the holder, the cap, or the open issue. Do not delete a lock to proceed.

### Ingesting a handoff

Reject and re-dispatch if any required section is missing, if commands are listed without exit codes,
or if the claimed commit doesn't exist in `git log`. A handoff with "ran the tests, all passing" and no
command output is not a handoff — it's a claim.

Then:
- Append `handoff_ingested` to `journal.jsonl` with the commit sha and the feature's **range**
  (`<base>..<head>`: head is the handoff's commit; base is the previous feature's head, or the
  merge-base with `origin/main` for the first). The hooks already journaled `dispatch` and
  `agent_return` with the measured duration — do not write those by hand.
- Materialise the reviewer's patch now, while the range is fresh:
  `bash "${CLAUDE_PLUGIN_ROOT}/scripts/mission-patch.sh" .missions/<slug> F00n <base> <head> -- <paths>`
- Update `features.md`: status, and `- **Range:** <base>..<head>`.
- Update `contract.md` assertions to `claimed` — **never** `proven`.
- Copy every issue from the handoff into `state.md` under open issues; rewrite `resume_next`.

## VALIDATE — at every milestone

Run in this order. Cheap and deterministic first; expensive and real last.

**1. Scrutiny** (`mission-validator-scrutiny`, one agent) — the repo's test layers, linters and type
checkers. Raw output and exit codes only; it makes no repairs.

**2. Blind review** (`mission-reviewer`, **one per feature**) — each gets its feature's
**materialised patch file**, that feature's assertions, and that feature's design guidelines. It must
**not** receive the handoff, the worker's reasoning, or the other reviewers' findings. Blindness is
the entire value; leaking context here quietly converts an independent check into an echo. The
design guidelines do not break blindness — they were written before the code, like the assertions.

Why a patch file and not a git command: on a file touched by many features,
`git diff origin/main...HEAD -- path` is the *cumulative branch*, not the feature — reviewers were
grading the whole branch under one feature's name — and `git log`/`git show` surface the author's
commit body, which is the reasoning blindness exists to withhold. The blind-review hook rejects a
reviewer prompt that names a git command or omits the patch path.

Reviewers hold the execution lease (they may run tests), so they run **one at a time** on this
host; dispatch the next when the previous returns. Static research may fan out meanwhile.

```
Agent tool (one call per feature):
  subagent_type: "mission-reviewer"
  prompt: |
    Mission: <slug>. Feature: F00n — <title>.
    Review the patch for F00n against these assertions. You have not seen how or why it was
    written and you should not go looking.
      A003 — <text>  proof budget: <min … ; max …>
      A007 — <text>  proof budget: <min … ; max …>
    Design guidelines this feature was bound to (pre-code, from design.md):
      D001 — <text> — exemplar `path/file.py:40`
    Patch: .missions/<slug>/patches/F00n.patch (base <sha>, head <sha>)  — read this file;
    do not run git log, git show or git diff yourself.
    Return a per-assertion verdict (satisfied / not satisfied / cannot tell from the diff),
    a per-guideline conformance verdict, plus defects with file:line and a root-cause
    cluster hint. "cannot tell" is a legitimate and useful answer.
```

A design-conformance failure is a defect like any other: it becomes a follow-up feature, never a
patch — and never a silent rewrite of the guideline to match the code.

**3. Behavior** (`mission-validator-behavior`) — only for assertions tagged `conversational` or
`interface`. Places real calls / drives the UI. Slow, costs money, capped per milestone in
`mission.md`. Skip it entirely if the milestone has no such assertions.

**4. Negotiate.** Read every verdict, then decide:

| Situation | Action |
|---|---|
| All milestone assertions proven | Run the convergence gate (below). If it passes: advance, mark `proven` in `contract.md` with the evidence reference, archive the milestone's sections. |
| Defect found | Register it in `followups.md` — severity, **cluster** by root cause, blocking flag, disposition. One cluster → one repair feature; one-line repairs sharing a cause are batched. Never patch it yourself. |
| Proof beyond an assertion's `max` budget suggested | Accepted debt: a `followups.md` entry with disposition `accept`. Not a feature. |
| Validator says "cannot tell" | Ambiguous assertion. Sharpen the wording, re-validate — don't wave it through. |
| Same assertion failed twice | **Classify before halting.** Root cause is one of: contract ambiguity / implementation defect / inadequate evidence / bad brief / environment. Only the first — or a fix that would weaken an assertion or change user-visible scope — is a BLOCK halt. The rest are repaired (repair-round cap permitting) under a journaled `decision`. The last mission escalated an incomplete *brief* to a human contract decision, and it helped end the mission. |
| Contract turned out to be wrong | **BLOCK halt and ask the user.** Never silently rewrite an assertion to match the code. |

**Convergence gate** — `bash "${CLAUDE_PLUGIN_ROOT}/scripts/mission-converge.sh" .missions/<slug> M<n>`
before advancing. It fails when cumulative follow-ups exceed features, when the per-milestone ratio
has risen two milestones running, or when the milestone introduced `interface`/`conversational`
assertions and proved none. A failure is a BLOCK halt with a re-plan — **never a cap raise**;
raising a cap against a diverging queue is the anti-pattern the last mission demonstrated.

Expect validation to fail on the first pass at every milestone. That's the normal, healthy case — it
is what you're paying for, and it is therefore **advisory by definition**: register the findings,
schedule the repair, proceed. A milestone that passes everything first try deserves a look at whether
the assertions are actually biting.

## Halts — BLOCK stops; ADVISORY proceeds

The last mission was idle for ~85% of its nine days, and 13 of its 15 long waits ended with the
human typing "continue". Most of those halts needed no decision. Two classes, then:

**BLOCK** — stop, write the decision card, wait:
- A cap in `mission.md` reached — dollars, dispatches, wall-clock, repair rounds (the guard enforces these)
- The convergence gate failed
- A feature needs a migration, or any write against a shared database
- A feature would cause a real side effect outside the repo — outbound calls, emails, payments,
  writes to a third-party system
- Root-cause classification says the *contract* is wrong, or the fix would weaken an assertion or
  change user-visible scope
- The autonomy ceiling is `halt at every milestone` and a milestone just closed
- Anything that would require pushing, merging, or force

**ADVISORY** — record and proceed: journal `{"event":"halt","class":"advisory","reason":…,
"assumption":…}`, write the assumption into `resume_next`, act on it, and surface it in the next
human-facing report. This covers: first-pass validation failure; "cannot tell"; a design-conformance
defect; an open handoff issue that a registered follow-up resolves; a milestone boundary under the
default `advisory` ceiling; anything else that is reversible, in-contract, side-effect-free and
within budget. If you are about to stop for something not on the BLOCK list, you are asking to be
driven.

**The decision card.** Every BLOCK halt — and every question to the human — has this shape, in this
order: the **symptom** in one plain sentence → **one worked example with real values** (a call, a
row, a screenshot description) → the **options**, each with its consequence → your
**recommendation** → only then the ids (A0nn, F0nn, FU0nn). Both frustration events in the last
mission followed dense, id-first explanations. Write it into `state.md` (`resume_next` carries the
recommendation), journal it with `class: block`, and stop. Do not park in a retry loop.

## Terminal steps

When every assertion is `proven` and `followups.md` is empty or explicitly accepted:

1. Whatever pre-merge sweep this repo defines (`/pre-merge` or equivalent), if it has one.
2. Update the docs the mission's changes invalidated — the project's wiki, its `docs/`, its README.
   Leave the codebase better than you found it.
3. Set `phase: pr` in `state.md`'s fenced block. This is the only phase in which pushing is
   allowed, and it exists because everything below needs a branch the review tools can see. The
   dollar cap's terminal-review reserve is released to `mission-reviewer` in this phase.
4. **Run `/missions:mission-pr-review`** — the whole-branch review pass. Every review so far was per-feature
   and blind, which is also its blind spot: nothing has yet looked at the whole branch at once. The
   skill opens the **draft** PR (via the repo's own PR skill), runs `/simplify`, fires the general
   and adversarial reviews, assesses every finding with read-only agents, routes survivors to
   `followups.md`, and writes `validation/pr-review-report.html`. It keeps its own progress file at
   `validation/pr-review.md`, so an interrupted pass re-enters where it stopped. A draft PR that
   nobody reviewed is not a terminal state.
5. Final report: assertions proven, defects caught by blind review that the workers' own tests missed
   (this number is the honest measure of whether the workflow earned its keep), spend vs cap
   (measured — `mission-spend.sh`), everything in `followups.md`, the PR-review verdict, and the
   five acceptance metrics: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/journal-metrics.sh" .missions/<slug>`.
6. Set `phase: done`. The hooks go inert for this mission.

Then stop. A human merges.
