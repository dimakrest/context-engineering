---
name: mission-resume
description: Rehydrate a mission after a context compaction, a /clear, a crash, or a new day. Reconstructs position from the mission directory, reconciles it against git, and hands control back to /missions:mission-run. Use when the user says "resume the mission", "/missions:mission-resume", or when mission state and reality may have diverged.
user_invocable: true
---

# /missions:mission-resume — reconstruct position from disk

A mission is designed to outlive the session that started it. This skill is the proof: everything
needed to continue is on disk, and nothing is needed from anyone's memory.

**Trust files and git, never recollection.** If you find yourself reasoning from what you think
happened earlier in the conversation, stop — read the file instead.

## Step 1 — read the mission directory

`mission.md` (goal, non-goals, constraints, seats, budget cap), `state.md`, `contract.md`,
`design.md`, `features.md`, every file in `handoffs/` and `validation/`, `followups.md`, and the
tail of `journal.jsonl`.

## Step 2 — reconcile against reality

This is the whole point of the skill. Check, don't assume:

| Check | Command | If it disagrees |
|---|---|---|
| On the mission branch? | `git branch --show-current` | Switch to it, or halt if the tree is dirty with unknown work |
| Working tree clean? | `git status --short` | Uncommitted work means a worker died mid-feature — see step 3 |
| Last commit matches the last handoff? | `git log --oneline -5` | Git wins. Correct `state.md` and journal the correction. |
| Branch behind main? | `git fetch origin main && git diff origin/main...HEAD` | Three dots, never two. Report the drift; don't merge without asking. |
| Nothing was pushed? | `git log origin/<branch>..HEAD` | If something was pushed and the phase is not `pr`, halt and tell the user — that violates a mission invariant. In the `pr` phase a pushed branch is expected. |

## Step 3 — classify what you found

| Finding | Resume from |
|---|---|
| Contract exists but no `design.md` | Planning finished but the mandatory architecture step never ran (or died before writing its file). Continue with `/missions:mission-design` — never straight into `/missions:mission-run`, which will refuse anyway. |
| Clean tree, handoff present for the last feature | The next feature. Normal case. |
| Clean tree, **no** handoff for the last commit | Reconstruct the handoff from the diff and the commit, mark it `reconstructed`, and flag it — a reconstructed handoff is weaker evidence than a written one, because it was authored by someone who can see the code. |
| Dirty tree, partial feature | Do **not** commit it blind. Show the user the diff and ask: finish, or discard and re-dispatch the feature to a fresh worker. Re-dispatching is usually right — half-finished work in a stale context is exactly what a mission is designed to avoid. |
| Validation ran but no verdict file | Re-run that validator. Cheaper than guessing what it said. |
| `crosscheck/progress.md` exists with unchecked steps | An external review died mid-pass. Continue with `/missions:mission-crosscheck` — it reads its own progress file and skips what is done. If its step 4 audit never ran, the transcript is **unaudited, not clean**: re-audit before quoting anything from it. |
| Phase is `pr` | The loop is over; the mission died mid terminal review. Continue with `/missions:mission-pr-review` — it reads its own progress file (`validation/pr-review.md`) and skips what's already done. A pushed branch is **expected** in this phase, not an invariant violation. |
| An amendment stopped partway — mission files disagree with each other | Run `bash ${CLAUDE_PLUGIN_ROOT}/scripts/check.sh .missions/<slug>` before anything else. If it fails, a `/missions:mission-amend` pass died mid-edit and the plan is internally inconsistent: finish it with `/missions:mission-amend`, never dispatch a worker against it. A worker reads whichever file it was handed and cannot tell that the others disagree. |
| Mission halted | Surface the halt reason and the decision the user still owes. Do not resume past a halt on your own initiative. |

## Step 4 — hand back

Rewrite `state.md` to the reconciled truth — the fenced `mission-state` block first (`phase`,
`milestone`, `spend_usd` recomputed with `scripts/mission-spend.sh`, a fresh `resume_next`), then
the prose — append a `resume` entry to the journal, and check the hook-owned locks: a `.writer` or
`.lease` whose holder has an `agent_return` in the journal is stale; delete it and journal why.
Confirm `bash "${CLAUDE_PLUGIN_ROOT}/scripts/mission-state.sh" .missions/<slug>` prints cleanly.
Then report in five lines or fewer: where the mission is, what changed during reconciliation, and
the next action.

Then continue with `/missions:mission-run` — or with `/missions:mission-design` if `design.md` doesn't exist yet, or
with `/missions:mission-pr-review` if the phase is `pr`, where the loop is already over. Never resume straight into dispatching a worker without the state file first
agreeing with git — a mission that resumes onto a wrong assumption spends real money building the
wrong thing.
