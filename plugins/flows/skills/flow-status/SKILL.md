---
name: flow-status
description: Say where a flow of inner PRs run by the Orca router is - daemons, chains, the step each PR is at, live workers, unanswered questions, the last rings - from `router.py status`, `last`, `workers`, `progress` and the progress.html page. Use when the user asks how a flow or a chain is going, says "/flows:flow-status", or after a restart or compaction before deciding anything.
---

# /flows:flow-status — where a run is

Read-only: nothing here starts, stops or answers anything. Set `ROUTER_STATE`, or `SCRATCH` (the state is then
`$SCRATCH/router`), so the commands find the run.

```sh
R=${CLAUDE_PLUGIN_ROOT}/router/router.py
```

| Command | Shows | For |
|---|---|---|
| `$R status` | One screen: daemons, chains, the running worker's last heartbeat or the running script step, unanswered questions, the last three journal lines. | The coordinator. Start here. |
| `$R last [n]` | The last n rings again (default 1). | A screen lost to a compaction. |
| `$R workers` | Orca's own view: one line per worker that is live or still owes something. | Deciding whether a silent worker is gone (`fail` needs Orca to say so). |
| `$R progress` | Every inner PR of the plan (done, running, at a gate, paused, stopped, not started), each open PR's steps with their times and second attempts, what runs now, what waits for the coordinator. | The owner. |
| `open $ROUTER_STATE/progress.html` | The same view as a page that reloads itself every 15 s. | The owner, in a browser. |

`$R plan <plan.json>` once makes `progress` list the PRs not started yet too:
`{"title": "…", "prs": [{"id": "<pr>", "title": "…", "part": "<group>"}]}`.

## The page's banners

| Banner | Do |
|---|---|
| "The mailbox daemon is not running" | `$R init` |
| "The router last rewrote this page N min ago" | `$R status`: a daemon ended without a word. |
| "A ring has waited N min" | The coordinator's `wait` is probably not running: `$R last`, then `$R wait`. |

## Ring kinds

One line each; [`/flows:flow-run`](../flow-run/SKILL.md) says how to rule on them.

- `WAKE question` — a worker asked something; its chain keeps running.
- `WAKE gate` — a chain reached a gate (contract acceptance, merge) and waits for `resume`.
- `WAKE failed` — a worker reported `worker_done` with outcome failed; the chain paused.
- `WAKE check` — a worker succeeded but a check is not OK; the chain paused.
- `WAKE runner` — a step cannot start (a variable has no value, a spec cannot be read).
- `start unknown` — `worker-start` gave no readable answer; nothing is started twice.
- script step failed — a CI wait or the draft-PR step failed; the screen shows its last line.
- silent worker — a live worker sent no heartbeat for `ROUTER_SILENT_MIN` (default 20) minutes; nothing is stopped.
- `escalation`, `merge_ready`, `handoff`, `decision_gate` — a worker's message; nothing pauses.
- foreign `worker_done` — a worker the router did not start finished; it is not released.
- mailbox errors — the mailbox check failed 5 times in a row; the daemon keeps trying.
- `ACT:` — the doorbell exited 3: the mailbox daemon or a runner is dead.
