---
name: flow-run
description: Run or continue a flow of inner PRs through the Orca router - bind the Run, apply the flow file (the router then starts each PR when it is ready), open the live page, wait on the doorbell, and answer each ring (reply, retry, resume, fail) until the chains are done, then report. Use when you are the Orca coordinator of a multi-PR run, when the owner says "/flows:flow-run", "apply the flow", "continue the flow", or when a `router.py wait` screen has just woken you.
---

# /flows:flow-run — the coordinator's loop

The router sits between an Orca Run's mailbox and you. Its daemons release finished workers, start each
chain's next step, run the step's checks and collect each worker's logs, without a model. You are woken only
when something needs a decision. Everything below is one command per line; [FLOWS.md](../../docs/FLOWS.md)
has the full tables.

```sh
R=${CLAUDE_PLUGIN_ROOT}/router/router.py
```

## Where state lives

`$ROUTER_STATE`, default `$SCRATCH/router` (`SCRATCH` is the run directory, never inside a repository). Export one
of them in every terminal that runs `$R`. The daemons and the chains live there, not in this session: a compaction,
a `/clear` or a new terminal loses nothing. `$R --help` lists every file under it.

## 1. Bind the Run

Run these from the coordinator's Orca terminal (the router reads `ORCA_TERMINAL_HANDLE` and `ORCA_PANE_KEY`).

```sh
orca orchestration run-create --objective "<the run's goal>" --json   # once; skip it when the terminal already has a Run
$R init                                                              # records the Run, starts the mailbox daemon
```

The mailbox daemon becomes the only consumer of that Run's mailbox, so bind a Run of your own, never another
coordinator's. A later session, or another terminal taking the Run over: `$R init --run <run_id>`. Running chains
go on.

## 2. Apply the flow

The flow file is the plan: the PR graph, each PR's template and its variables. [`/flows:flow-plan`](../flow-plan/SKILL.md)
writes it with the owner. Check it, then apply it:

```sh
$R flow apply <flow.json> --dry-run                      # the change lines, or NOT OK and one line per problem
$R flow apply <flow.json> --by <you> --note "<why>"      # v1; under "start": "auto" every ready PR starts now
$R flow show                                             # one line per PR: running <step>, waiting for: <vars>, done…
```

A refusal changes nothing: fix the lines it prints and apply again. Every accepted apply is a new version with one
history row (`$R flow history`). To change the plan later, edit the file and apply it again: a pending step may be
edited, added, removed or reordered; a settled step, a started PR's `after` and a variable a settled step used
cannot change. Under `"start": "manual"`, `$R flow start <pr>` starts one ready PR.

Before a PR starts, the run directory must hold what its specs read: `$SCRATCH/intent/<ISSUE>.md`, the PR's worktree
(`WT`), and any file the profile names. The router creates none of them.

Without a flow, `$R chain <pr> --def ${CLAUDE_PLUGIN_ROOT}/router/templates/inner-pr.json WT=… ISSUE=… BASE_BRANCH=… TITLE=…`
still starts one PR by hand (FLOWS.md, "Without a flow").

## 3. Open the page

```sh
$R page --open           # the live page in an Orca browser tab; $R page prints the URL
```

The page shows the run as a graph of PRs with their step chips and the workers that run them, and edits the flow on
the same canvas: drag a pending step, drop a role from the palette, then Apply, which is `flow apply` with the same
checks and a history row. The owner may edit there while you run the loop; a stale edit is refused, never merged.

## 4. Wait

```sh
$R wait          # in the background, as the LAST action of every turn
```

It blocks until something needs you, prints one screen and exits; the harness then wakes you. Every
screen ends with a `next:` line naming the commands that fit. A screen lost to a compaction: `$R last`.

## 5. Rule on the ring

| Ring | Ruling |
|---|---|
| `WAKE question` | `$R reply <message_id> "<answer>"`. The chain never paused. |
| `WAKE gate` | Do what the gate's title says (accept the contract; for the merge gate the owner merges the PR), then `$R resume <pr>`. |
| `WAKE failed` | `$R retry <pr> --note "<what to do differently>"`; `--agent`, `--model`, `--effort` change who runs it. |
| `WAKE check` (a check is not OK) | Fix the cause and `$R resume <pr>` (re-runs the checks), or `$R retry <pr> --note "<the failing line>"`. |
| Take the tree as it is | `$R resume <pr> --accept "<why>"`; when the step was to set a variable, add `--set NAME=<value>`. |
| Send it back | `$R resume <pr> --from <step> --note "<text>"`: that step and every later one run again. |
| `WAKE runner` (a variable has no value) | `$R resume <pr> --set NAME=<value>`, unless the flow sets it: then apply the flow with the value. |
| `WAKE runner · the flow could not be read` | Fix the file the router's copy came from and apply it again; the runner waits. |
| `start unknown` | `$R workers`; if Orca started it, `$R resume <pr> --adopt <dispatch_id>`, else `$R retry <pr>`. |
| A silent worker | `$R workers`. Only when Orca shows it stopped, failed or exited: `$R fail <pr> --why "<text>"`, then `retry`. Silence alone is never enough. |
| `escalation`, `merge_ready`, `handoff`, `decision_gate` | Read the file the screen names, act, and `resume` if a chain waits on it. |
| `ACT:` (exit 3: a daemon died) | `$R init`, then `$R resume <pr>` for each chain `$R status` shows as `RUNNER GONE`. |

A dispute, a triage or any one-off job outside a chain: `$R worker "<label>" --spec-file <f> --agent claude --model <id> --pr <pr>`.
It rings when done, and its logs go under `logs/<pr>/_adhoc/`. Bounded loops (a second review round) are your
decisions: an ad hoc worker, then `resume`.

After every ruling, go back to step 4.

## 6. Logs, the report, and stopping

The mailbox daemon collects each released worker's logs into `$ROUTER_STATE/logs/` (`$R status` says `logs N/M
settled dispatches collected`); a `collector: <dispatch>: <why>` journal line is a collection that failed, never a
chain that did. When every chain is done, `/flows:flow-report` builds the report and reads its lessons back.

A chain is done when its last step passed (for `inner-pr.json`, the merge gate; the router does not check that the
merge happened). `$R stop <pr>` stops one runner, `$R stop --all` every daemon. Workers are never touched.

## Never

- Merge a PR, mark one ready for review, or switch the GitHub account: those are the owner's. The merge gate rings so
  that the owner decides.
- Answer a ring by editing `state.json`, `flow.json` or `journal.md` by hand: every change goes through a command.
- Bind the router to a Run another coordinator consumes.
