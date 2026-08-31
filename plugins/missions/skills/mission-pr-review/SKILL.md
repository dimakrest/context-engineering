---
name: mission-pr-review
description: The terminal whole-branch review pass of a mission, run in the `pr` phase. Ensures the draft PR exists, runs /simplify, fires the general code review and the repo's adversarial-review skill in parallel, assesses every finding with read-only agents, routes survivors to followups.md, and writes an HTML findings report. Invoked by /missions:mission-run's terminal steps, or by /missions:mission-resume when the phase is `pr`; re-entrant via its own progress file. Use when the user says "/missions:mission-pr-review" or asks to run or resume the mission's PR review.
user_invocable: true
---

# /missions:mission-pr-review — the whole-branch review

Every review during the mission was **per-feature and blind** — one diff, one set of assertions.
That is what makes it independent, and it is also its blind spot: nothing has yet looked at the whole
branch at once, so cross-feature interactions and the shape of the design as a whole are unreviewed.
This skill closes that gap. A draft PR that nobody reviewed is not a terminal state.

## Invariants — the mission's rules still apply here

- **You fix nothing.** Findings that survive assessment go to `followups.md`, never into the code.
  The one exception is step 2's `/simplify` pass, which applies its own quality findings — that is
  its defined job, and it runs *before* the reviews so its changes are themselves reviewed.
- **You never mark an assertion `proven`**, and you never merge — no `--no-verify`, no `--admin`.
- Pushing is allowed **only because the phase is `pr`** — the commit-discipline hook enforces this.
  Never edit the phase to unblock a push you want to make.

## Preconditions — check before spending anything

Read `.missions/<slug>/state.md` and `contract.md`:

- Every assertion is `proven`, and `followups.md` is empty or explicitly accepted. If not, refuse
  and point back to `/missions:mission-run` — this skill grades a finished branch, it does not finish one.
- `**Phase:**` is `pr`. If the conditions above hold but the phase isn't set yet (a standalone
  invocation), set it now and journal a `decision` — that is `/missions:mission-run`'s terminal step you are
  performing, not a dodge.

## Progress file — this skill is re-entrant

Maintain `.missions/<slug>/validation/pr-review.md`. Read it first, every time; skip any step it
records as done. The reviews here are slow (an adversarial run can take ~30 minutes) and a session
can die mid-pass — this file is how `/missions:mission-resume` re-enters without repeating paid work.

```markdown
# PR review — <slug>

**PR:** #123 (draft) | not opened yet
- [x] 1 draft PR opened — #123
- [x] 2 simplify — commit `abc123`; skipped findings → FU00n
- [ ] 3 reviews — /code-review <effort> · <repo adversarial skill, or "none defined">
- [ ] 4 findings assessed
- [ ] 5 report — validation/pr-review-report.html

## Findings
| # | Source | Finding | Verdict | Evidence | Disposition |
|---|---|---|---|---|---|
```

## The steps

**1. Draft PR.** If no PR exists for the mission branch: push the branch, then open a **draft** PR —
via the repo's own PR skill if it has one, otherwise `gh pr create --draft`. Use the repo's skill
even when you have a hand-written body: keep your body's substance and fit it to the repo's template
rather than choosing between them. A repo PR skill usually carries steps a `gh pr create` does not —
a security checklist, a QA comment on the issue, a change-size table — and skipping it silently drops
whatever review routing those steps trigger. The description carries the contract and the
assertion→evidence table, so a reviewer sees what "done" meant and how each part was proven.

**2. Simplify.** Run `/simplify` on the branch. It is a quality pass, not a bug hunt — reuse,
simplification, efficiency, altitude. Apply what it finds, then commit and push. Every finding it
*skipped* goes into `followups.md` with the reason it was skipped; a skipped finding that is not
written down is a finding that never happened.

**3. Review, in parallel.** Both of these at once, in one message:

- `/code-review` — pick the effort from the PR's actual complexity, not its line count: a wide
  mechanical rename is `low`, a change on an auth or money path is `high`. **Never `ultra`** — it is
  user-triggered and billed, and you cannot launch it.
- **The repo's adversarial-review skill, if the project defines one** — the mission's `state.md`
  standing constraints should name it. It challenges the *approach*, not the implementation — the
  only step in the whole mission that questions whether the design was the right call, because the
  contract fixed the design before any code existed. If the project defines none, skip it and say so
  in the report: the design pass did not run.

If a review tool refuses to run on a draft, flip the PR ready-for-review, run it, and flip it back
to draft when done. That is a visible state change on a PR your team can see — say so, and confirm
before doing it if the repo is one where a review request pages someone.

**4. Assess every finding.** A raw finding is a rumour — both tools produce findings that look
plausible and are wrong. For each finding that arrives *unassessed*, fan out one agent — read-only,
told to default to "refuted" when uncertain — to confirm or refute it against the actual code. Do
not re-assess what arrives already analyzed: an adversarial skill that ships per-finding verdicts
(as bell3's `/codex-adversarial-review` does) has done this step for its own findings — reuse those
verdicts. Findings that survive go to `followups.md`. **You do not fix what they find.**

**5. Report.** A self-contained HTML page at `.missions/<slug>/validation/pr-review-report.html`:
each finding, its source, the verdict, and the evidence. Lead with the verdicts, not the prose.
Then mark the progress file complete and journal the completion.

## Hand back

Return a short summary — findings by verdict, what landed in `followups.md`, and the report path —
for `/missions:mission-run`'s final report (or for the user, if invoked directly). Nothing in this skill may
fix a defect, merge, or mark an assertion `proven`.
