---
name: flow-run
description: Run or continue a flow of inner PRs through the Orca router - bind the Run, start one chain per PR from a template, wait on the doorbell, and answer each ring (reply, retry, resume, fail) until the chains are done. Use when you are the Orca coordinator of a multi-PR run, when the user says "/flows:flow-run", "start the chain", "continue the flow", or when a `router.py wait` screen has just woken you.
---

# /flows:flow-run — the coordinator's loop

The router sits between an Orca Run's mailbox and you. Its daemons release finished workers, start each
chain's next step and run the step's checks without a model. You are woken only when something needs a
decision. Everything below is one command per line; read [the plugin's README](../../README.md) for the
full tables.

```sh
R=${CLAUDE_PLUGIN_ROOT}/router/router.py
T=${CLAUDE_PLUGIN_ROOT}/router/templates
```

## Where state lives

`$ROUTER_STATE`, default `$SCRATCH/router` (`SCRATCH` is the run directory). Export one of them in every
terminal that runs `$R`. The daemons and the chains live there, not in this session: a compaction, a
`/clear` or a new terminal loses nothing. `$R --help` lists every file under it.

## 1. Bind the Run

Run these from the coordinator's Orca terminal (the router reads `ORCA_TERMINAL_HANDLE` and `ORCA_PANE_KEY`).

```sh
orca orchestration run-create --objective "<the run's goal>" --json   # once; skip it when the terminal already has a Run
$R init                                                              # records the Run, starts the mailbox daemon
```

A later session, or another terminal taking the Run over: `$R init --run <run_id>`. Running chains go on.

## 2. Start a chain per PR

```sh
$R chain <pr> --def $T/inner-pr.json --dry-run WT=<worktree> ISSUE=<n> BASE_BRANCH=<branch> TITLE="<pr>: <goal>"
$R chain <pr> --def $T/inner-pr.json WT=<worktree> ISSUE=<n> BASE_BRANCH=<branch> TITLE="<pr>: <goal>"
```

Dry-run first: it lists the steps and names every variable with no value. `BASE_BRANCH` has no default on
purpose, so a chain never merges into the last run's branch. `"variables"` in the template says what each
one means (`RUN_CONTEXT`, `TESTS`, `LEDGER`, `GREEN_PINNED`, `IMPL_EFFORT`, `EXTRA_SUITE`, `GOLDENS`). At most
`ROUTER_MAX_CHAINS` (default 2) chains run at once; a chain holds its slot until its last step, also at a
gate. `$T/smoke.json` with `OUT=<empty dir>` proves the router against real Orca on harmless work.

## 3. Wait

```sh
$R wait          # in the background, as the LAST action of every turn
```

It blocks until something needs you, prints one screen and exits; the harness then wakes you. Every
screen ends with a `next:` line naming the commands that fit. A screen lost to a compaction: `$R last`.

## 4. Rule on the ring

| Ring | Ruling |
|---|---|
| `WAKE question` | `$R reply <message_id> "<answer>"`. The chain never paused. |
| `WAKE gate` | Do what the gate's title says (accept the contract, merge the PR), then `$R resume <pr>`. |
| `WAKE failed` | `$R retry <pr> --note "<what to do differently>"`; `--agent`, `--model`, `--effort` change who runs it. |
| `WAKE check` (a check is not OK) | Fix the cause and `$R resume <pr>` (re-runs the checks), or `$R retry <pr> --note "<the failing line>"`. |
| Take the tree as it is | `$R resume <pr> --accept "<why>"`; when the step was to set a variable, add `--set NAME=<value>`. |
| Send it back | `$R resume <pr> --from <step> --note "<text>"`: that step and every later one run again. |
| `WAKE runner` (a variable has no value) | `$R resume <pr> --set NAME=<value>`. |
| `start unknown` | `$R workers`; if Orca started it, `$R resume <pr> --adopt <dispatch_id>`, else `$R retry <pr>`. |
| A silent worker | `$R workers`. Only when Orca shows it stopped, failed or exited: `$R fail <pr> --why "<text>"`, then `retry`. Silence alone is never enough. |
| `escalation`, `merge_ready`, `handoff`, `decision_gate` | Read the file the screen names, act, and `resume` if a chain waits on it. |
| `ACT:` (exit 3: a daemon died) | `$R init`, then `$R resume <pr>` for each chain `$R status` shows as `RUNNER GONE`. |

A dispute, a triage or any one-off job outside a chain: `$R worker "<label>" --spec-file <f> --agent claude --model <id>`.
It rings when done. Bounded loops (a second review round) are your decisions: an ad hoc worker, then `resume`.

After every ruling, go back to step 3.

## 5. Stop

A chain is done when its last step passed (for `inner-pr.json`, the merge gate; the router does not check that
the merge happened). `$R stop <pr>` stops one runner, `$R stop --all` every daemon. Workers are never touched.
