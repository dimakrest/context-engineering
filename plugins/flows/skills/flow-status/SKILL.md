---
name: flow-status
description: Say where a flow of inner PRs run by the Orca router is - the flow's version and each PR's state, daemons, the step each chain is at, live workers, unanswered questions, the page's URL, and how many settled workers' logs are collected - from `router.py status`, `flow show`, `flow history`, `last`, `workers` and `page`. Use when the owner asks how a flow or a chain is going, says "/flows:flow-status", or after a restart or compaction before deciding anything.
---

# /flows:flow-status — where a run is

Read-only: nothing here starts, stops, applies or answers anything. Set `ROUTER_STATE`, or `SCRATCH` (the state is
then `$SCRATCH/router`), so the commands find the run.

```sh
R=${CLAUDE_PLUGIN_ROOT}/router/router.py
```

## Start here

```sh
$R status
```

One screen. Report these lines to the owner, in this order:

| Line | Says |
|---|---|
| `router` | the Run, whether the mailbox daemon is alive, its last delivery, rings waiting |
| `flow` | `v<N> · slots <used>/<n> · start <auto\|manual>`, when a flow was applied |
| `page` | **the page's URL** (`http://127.0.0.1:<port>/`), served by the mailbox daemon |
| one line per chain | its state, steps done of all, the step running, its dispatch, the worker's last heartbeat |
| `question` | a question nobody answered, with its reply command |
| `logs` | **`<collected>/<settled> settled dispatches collected`**: the workers whose logs are in `logs/`, of those that settled |
| `journal` | the last three journal lines |

When collected is less than settled and the run is not live, say so and point to `/flows:flow-report`, which
collects the rest first. A `collector:` journal line names a dispatch that failed to collect.

## Then, as needed

| Command | Shows | For |
|---|---|---|
| `$R flow show` | `flow v<N> · slots · start`, then one line per PR: id · part · title · after · state (`not started · after <ids>`, `waiting for: <vars>`, `ready`, `running <step>`, `at a gate`, `paused`, `stopped`, `done`, `removed in v<N>`) | Which PRs wait, and for what. |
| `$R flow history [n]` | The last n versions: who, when, why, the change lines. | What changed in the plan, and who changed it (the page's applies say `by page`). |
| `$R page` | The page's URL; `$R page --open` opens it in an Orca browser tab. | The owner, live: the PR graph, step chips, workers, rings, the flow's versions. |
| `$R last [n]` | The last n rings again (default 1). | A screen lost to a compaction. |
| `$R workers` | Orca's own view: one line per worker that is live or still owes something. | Deciding whether a silent worker is gone (`fail` needs Orca to say so). |
| `$R progress` | Every PR of the flow, each open PR's steps with their times and second attempts, what runs now, what waits. | The owner, as text. `$ROUTER_STATE/progress.html` is the same view as a file that reloads itself. |
| `$R collect --dry-run` | Each settled dispatch's session match (`unique`, `ambiguous`, `none`) and the counts; copies nothing. | Why a dispatch has no tokens. |

A run started without a flow lists only the PRs whose chain exists (or those of an old `plan.json`); `flow show` and
`flow history` then say there is no flow.

## The page's banners

| Banner | Do |
|---|---|
| "The mailbox daemon is not running" | `$R init` |
| "The router last rewrote this page N min ago" | `$R status`: a daemon ended without a word. |
| "A ring has waited N min" | The coordinator's `wait` is probably not running: `$R last`, then `$R wait`. |
| The router has not answered for 30 s (the live page) | The daemon that serves it has probably stopped: `$R status`. |

## Ring kinds

One line each; [`/flows:flow-run`](../flow-run/SKILL.md) says how to rule on them.

- `WAKE question` — a worker asked something; its chain keeps running.
- `WAKE gate` — a chain reached a gate (contract acceptance, merge) and waits for `resume`.
- `WAKE failed` — a worker reported `worker_done` with outcome failed; the chain paused.
- `WAKE check` — a worker succeeded but a check is not OK; the chain paused.
- `WAKE runner` — a step cannot start (a variable has no value, a spec cannot be read, the flow could not be read).
- `start unknown` — `worker-start` gave no readable answer; nothing is started twice.
- script step failed — a CI wait or the draft-PR step failed; the screen shows its last line.
- silent worker — a live worker sent no heartbeat for `ROUTER_SILENT_MIN` (default 20) minutes; nothing is stopped.
- `escalation`, `merge_ready`, `handoff`, `decision_gate` — a worker's message; nothing pauses.
- foreign `worker_done` — a worker the router did not start finished; it is not released.
- mailbox errors — the mailbox check failed 5 times in a row; the daemon keeps trying.
- `ACT:` — the doorbell exited 3: the mailbox daemon or a runner is dead.
