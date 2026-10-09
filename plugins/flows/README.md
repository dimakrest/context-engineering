# flows: the Orca router

A Claude Code plugin for running a flow of inner PRs through Orca workers. Its core is the router: scripts that
sit between an Orca Run's mailbox and the coordinator. No model runs in them. Two skills drive it:
`/flows:flow-run` (the coordinator's loop) and `/flows:flow-status` (where a run is).

```sh
/plugin install flows@dimakrest-context-engineering
```

The router was extracted from a Bell pipecat-upgrade run. In that run (group B, 2026-09-28 to 10-01) a waiter
script already kept heartbeats away from the coordinator, but every `worker_done` still woke it: 102 of them,
plus 5 questions. Each time it read the result, released the terminal, acknowledged the delivery, wrote the
journal line and started the next worker by hand. The router does those steps. The coordinator is rung only when
something needs a decision.

Contents: [Parts](#parts) · [Try it](#try-it) · [The coordinator's loop](#the-coordinators-loop) ·
[The flow file](#the-flow-file) · [Seeing progress](#seeing-progress) · [Every worker's logs](#every-workers-logs) · [What rings](#what-rings) ·
[A chain definition](#a-chain-definition) · [Repository profiles](#repository-profiles) ·
[What the run provides](#what-the-run-provides) · [Checks](#checks) · [After a restart](#after-a-restart) ·
[Tested how](#tested-how) · [Not tested, and limits](#not-tested-and-limits)

## Parts

| File | What it is |
|---|---|
| `router/router.py` | The mailbox daemon, the chain runner, the doorbell and the coordinator's commands. `router.py --help` prints all of it. Python 3.9+, standard library only. |
| `router/collector.py` | Gathers each settled worker's logs whole: Orca's archive of it, its events, and the agent session file that ran it, matched and copied, with its token usage summed. See [Every worker's logs](#every-workers-logs). |
| `router/progress.py` | Renders the owner's view: the text of `router.py progress` and `progress.html`. It reads nothing itself. |
| `router/templates/inner-pr.json` | One inner PR of a larger change as a chain: 17 steps, two gates (contract acceptance, merge). `--dry-run` lists the steps and names anything missing. |
| `router/specs/*.md`, `router/contract-template.md` | The 12 role specs the chain names, one per worker step, in Orca's shape (target, change, constraints, ownership, observable acceptance); `arbiter.md`, a template the coordinator fills for a test dispute; and the template every contract follows. |
| `router/profiles/*.json` | What differs between repositories (test, lint and commit commands, guarded paths, the rules file): `python`, `typescript`, `bell`. See [Repository profiles](#repository-profiles). |
| `router/templates/smoke.json`, `router/specs/smoke-*.md` | A harmless chain that proves the router against real Orca: two workers at once, a question, a failure with a retry, a script step, a gate. |
| `router/checks/*` | Checks that print one line, `OK …` or `NOT OK …`, and exit 0 or 1. |
| `router/draft-pr.sh` | Opens the inner PR as a draft, so that step needs no model. Refuses `main` as the base. |
| `tests/` | `test_router.py` (145 tests), `fake-orca`, a stand-in for the Orca CLI, and synthetic session files under `fixtures/collector/`. |
| `skills/flow-run`, `skills/flow-status` | The two skills. |

State lives in `$ROUTER_STATE` (default `$SCRATCH/router`, where `SCRATCH` is the run directory). `router.py --help`
lists the files.

Below, `$FLOWS` is the plugin's directory: `${CLAUDE_PLUGIN_ROOT}` inside Claude Code, `plugins/flows` in a checkout
of this repository.

## Try it

The smoke chain runs today, from any Orca terminal (the router reads `ORCA_TERMINAL_HANDLE` and `ORCA_PANE_KEY`
from it). It starts 5 Sonnet workers, takes about five minutes and writes only under `OUT`.

```sh
R=$FLOWS/router/router.py
export ROUTER_STATE=$HOME/.cache/router-smoke/state; mkdir -p $HOME/.cache/router-smoke/out
orca orchestration run-create --objective "router smoke" --json     # skip it when the terminal already has a Run
$R init
$R chain smoke --def $FLOWS/router/templates/smoke.json OUT=$HOME/.cache/router-smoke/out
$R wait                                                             # blocks, then prints one screen
```

It rings three times. Answer each, then run `$R wait` again:

| Ring | Answer |
|---|---|
| `WAKE question … PASS or FAIL?` | `$R reply <message id from the screen> PASS` |
| `WAKE failed · smoke · flaky` | `$R retry smoke --note "write the word OK into flaky.txt in the target directory"` |
| `WAKE gate · smoke · accept` | `$R resume smoke` |

`$R status` then shows the chain as done. `$R stop --all` ends the mailbox daemon.

## The coordinator's loop

```sh
export SCRATCH=<the run directory>                        # the router's state is $SCRATCH/router
R=$FLOWS/router/router.py

orca orchestration run-create --objective "…" --json      # once; a later session: $R init --run <run_id>
$R init                                                   # records the Run, starts the mailbox daemon
$R chain <pr> --def $FLOWS/router/templates/inner-pr.json WT=<worktree> ISSUE=<n> BASE_BRANCH=<branch> TITLE="<pr>: …"   # one per PR slot
$R wait                                                   # in the background, as the LAST action of every turn
```

A run planned as a [flow file](#the-flow-file) replaces the `chain` lines: `$R flow apply <flow.json>` once, and
the router starts each PR when it is ready.

`/flows:flow-run` walks through the same loop.

`wait` blocks until something needs the coordinator, prints one screen and exits; the harness then wakes the
coordinator. Every screen ends with a `next:` line. The answers:

| Command | When |
|---|---|
| `$R resume <pr>` | After a gate, once decided. After a check that was not OK and has been dealt with: it runs the checks again. |
| `$R resume <pr> --accept "<why>"` | Go on past a failed step or a check that is not OK, taking the tree as it is now. The reason goes into the journal. When the step was to set a variable (`draft_pr` sets `PR_URL`), give it: `--set PR_URL=<value>`. |
| `$R resume <pr> --from <step> --note "<text>"` | Send it back: that step and every later one run again, and the note is added to the step's spec. Refused while a worker of one of those steps is live. |
| `$R resume <pr> --adopt <dispatch_id>` | After a `start unknown` ring: `$R workers` shows whether Orca started the worker. If it did, this takes it over; if not, `$R retry <pr>`. |
| `$R retry <pr> --note "<text>"` | Run the failed step again with a fresh worker. `--agent`, `--model`, `--effort` change who runs it (Codex could not start: `--agent claude --model claude-fable-5-1`). |
| `$R fail <pr> [--step <id>] --why "<text>"` | A worker ended without `worker_done`, so its step would run for ever. This marks the step failed, so `retry` works. Refused unless Orca shows that worker stopped, failed, abandoned or exited: silence alone is never enough. |
| `$R resume <pr> --set NAME=<value>` | After a `WAKE runner` ring: a later step needs a variable that nothing set. |
| `$R reply <message_id> "<answer>"` | Answer a worker's question. Its chain never paused. |
| `$R worker "<label>" --spec-file <f> --agent claude --model <id>` | A worker outside a chain: an arbiter, a triage of a failure. It rings when done. |
| `$R status` | One screen: daemons, chains, the running worker's last heartbeat or the running script step, unanswered questions, the last three journal lines. |
| `$R last [n]` | Prints the last n rings again (default 1), for a screen lost to a compaction. |
| `$R workers` | Orca's own view, one line per worker that is live or still owes something. |
| `$R stop <pr>` / `$R stop --all` | Stops a runner or every daemon. Workers are untouched. |

## The flow file

One JSON file can plan the whole run: the PR graph, each PR's steps, the variables. `router.py flow apply` is the
only way it enters or changes a run, and every accepted apply is a new version. Each PR's runner follows the latest
version for the steps it has not started, and never touches work in flight. Without a flow, `plan` and one `chain`
per PR work as above.

```json
{
  "title": "Move the payments client to v3",
  "slots": 2,
  "start": "auto",
  "templates": {"inner-pr": "<FLOWS>/router/templates/inner-pr.json"},
  "profile": "<FLOWS>/router/profiles/python.json",
  "vars": {"RUN_CONTEXT": "The payments client moves from v2 to v3."},
  "prs": [
    {"id": "p1", "part": "Part 1 · Dependencies", "title": "Pin the new client", "base": "payments-v3", "after": [],
     "template": "inner-pr", "vars": {"WT": "{SCRATCH}/wt/{PR}", "ISSUE": "12", "TITLE": "p1: pin the new client"}},
    {"id": "p2", "part": "Part 2 · Callers", "title": "Switch the checkout flow", "base": "payments-v3", "after": ["p1"],
     "template": "inner-pr", "vars": {"ISSUE": "13", "TITLE": "p2: switch the checkout flow", "IMPL_EFFORT": "xhigh"}}
  ]
}
```

| Field | Meaning |
|---|---|
| `title` | Shown by `flow show` and the progress view. |
| `slots` | Chains open at once (default `ROUTER_MAX_CHAINS`). With a flow it also caps chains started by hand. |
| `start` | `auto`: the router starts a PR as soon as it is ready. `manual`: only `router.py flow start <pr>` does. Default `auto`. |
| `templates` | Name → chain definition file, relative to the flow file or absolute. `<FLOWS>` above stands for the plugin's directory, written out. |
| `profile` | Optional: a [repository profile](#repository-profiles), `router/profiles/<name>.json` or a repository's own, relative to the flow file or absolute. Its `vars` are the lowest layer, and the `{SCRATCH}`, `{WT}` and `{PR}` in them are filled in. `inner-pr.json` defaults none of them, so a flow of inner PRs names one. |
| `vars` | Variables for every PR. |
| `prs[].id` | The `<pr>` of the chain: letters, digits, `.`, `_` and `-`, starting with a letter or digit. Upper and lower case are the same. |
| `prs[].part`, `title` | The progress view's group and line. |
| `prs[].base` | The branch the PR merges into. It becomes `BASE_BRANCH`. Never `main` or `master`. |
| `prs[].after` | Ids of the PRs whose chain must be done before this one starts. |
| `prs[].template` | A name from `templates`. |
| `prs[].vars` | The PR's own variables. |
| `prs[].steps` | Optional: the PR's full step list, in a template's `steps` shape. It replaces the template's steps; the template still gives the kit and its `vars`. The chain gets it as `chains/<pr>/def.json`. |

Variables, lowest first: the profile's, the template's `vars`, the flow's, the PR's, then the router's own: `PR`,
`STATE`, `DEF_DIR` (the template's directory), `KIT`, `CHECKS`, and `BASE_BRANCH` from `base`. Below them all,
`SCRATCH` from the environment of `flow apply` fills in when nobody sets it. None of the router's own can be given in `vars`. A value may name another variable:
`"WT": "{SCRATCH}/wt/{PR}"` is filled in. A name that no layer gives and the template's `"variables"` does not declare
is refused as a typo, and the line names the layer the value came from: `vars.X` for the flow's, `A1: vars.X`,
`template inner-pr: vars.X`, `profile bell: vars.X`. A declared name that nobody gives (`WT` most often) makes the PR
wait, also when only a value names it: `flow show` says `waiting for: WT`, and the PR does not start until an apply
gives it.

The order of `prs` is part of the version: the scheduler starts ready PRs in it, and `flow show` and the progress
view list them in it. An apply that only reorders them is a new version, with `PRs reordered: B1, A1` in its history.
A started PR may move too; the order only decides which ready PR takes the next free slot.

The router's copy is `$ROUTER_STATE/flow.json`: the file as applied, plus keys only the router writes. A file given to
`apply` may carry them; they are ignored.

| Key | Meaning |
|---|---|
| `version` | 1, then one more per accepted apply. |
| `history` | One row per accepted apply: `{"v", "at", "by", "note", "changes": [one line per change]}`. |
| `dir` | The directory the flow's relative paths were resolved from. |
| `resolved` | What the apply read: each template (`path`, `kit`, its definition), the profile (`path`, `name`, `vars`), and `SCRATCH` from its environment. A version never changes after it is written. A template edited on disk counts only when the next apply reads it, and that apply lists the edit as step changes. |
| `removed` | The PRs a version dropped before they started: `{"id", "part", "title", "v"}`. |

### What `flow apply` refuses

Each problem is one line, and a refusal changes nothing: no version, no history row, no journal line.

- **In the file:** an unknown template name; a template or profile that is not a file; a cycle in `after`; an `after`
  that names no PR; one id twice (upper and lower case are the same); an id `chain` would refuse; a `base` of `main`
  or `master`; a value that names an unknown variable; a step list the chain validator rejects (the one `chain`
  uses); `slots` that is not a positive integer; `start` that is not `auto` or `manual`; `vars` that set a variable
  the router sets.
- **Against the run:** `--base <v>` that is not the current version (`stale: the run is at v<N>`; the page will
  send it). For a PR whose chain exists:
  - a settled step (one that is not pending, or that its runner is about to start) edited, removed or moved, or a
    step put before it;
  - a changed value of a variable a settled step used: the `{NAME}`s of its definition and of its spec, `WT` and
    `KIT` for a worker step, `KIT` for a step with a command. The spec's names are read from the spec as it is on
    disk at the apply, not as the step ran: the copy's snapshot holds the templates and the profile, not the specs;
  - a changed `after`, or the PR removed.

  A pending step may be edited, added, removed or reordered freely. A chain started with `router.py chain` cannot be
  taken over by a flow that names it.

### What runs

- **The scheduler.** Under `"start": "auto"`, every PR that is ready starts, in the flow's order. Ready means it has
  no chain yet, every PR it is `after` is done, a slot is free, and its definition has a value for every variable.
  It starts as `chain` would start it, and the journal says `flow: started <pr> (v<N>)`. Three things run a
  scheduler pass: the mailbox daemon once per long-poll, `flow apply`, and a runner whose chain just completed.
  `flow start <pr>` runs one for that PR only, and is the only way under `manual`. A PR that is not ready is not
  journaled at every tick: `flow show` and the progress view say what it waits for. The passes take turns under
  `flow.lock`, so a PR starts once.
- **The runner, at every step boundary.** Before it starts the next step, it re-reads the flow. The chain becomes
  [its settled steps as they are] + [the flow's other steps, in the flow's order, with the flow's definitions]. Its
  variables become the flow's, except those a step exported and the router's `HEAD_*`. The journal says what
  changed: `flow v3: step edited: model … -> …`, `step added after g`, `step removed; it will not run`. Each attempt
  records `flow_version`, `cause` (`first`, `retry`, `resume_from`), and `edited: true` when its step's definition
  is not the one the chain was created with. A recheck is not a new attempt; it adds
  `{"at", "flow_version", "cause": "recheck"}` to the attempt's `rechecks`.
- **A flow that cannot be read.** The runner pauses and rings `WAKE runner · <pr>: the flow could not be read: <why>`.
  The daemon rings once for each reason and goes on.
- `resume --set` refuses a variable the flow sets, because the next re-read would replace it. Change it with an
  apply.

| Command | Prints |
|---|---|
| `$R flow apply <file> [--base <v>] [--by <who>] [--note "<text>"]` | `OK flow v<N>: <n> changes` and one line per change, then any PR it started; `OK flow v<N>: no change …` for the current version's file; or `NOT OK flow: <n> problem(s), nothing changed` and one line per problem (exit 1). |
| `$R flow show` | `flow v<N> · slots <used>/<n> · start <auto\|manual>`, then one line per PR: id · part · title · after · state. The state is `not started · after <ids>`, `waiting for: <vars>`, `ready`, `running <step>`, `at a gate`, `paused`, `stopped`, `done`, or `removed in v<N>`. |
| `$R flow start <pr>` | `OK started <pr> (v<N>), runner pid …`, or why it is not ready. |
| `$R flow history [<n>]` | The last n versions (default 10): `v<N> · <when> · by <who> · <n> changes · <note>`, then the changes. |

With a flow, `router.py chain <pr>` refuses the PRs the flow names (`the flow owns <pr>: router.py flow start <pr>`),
`plan` is refused (`the flow is the plan`), and the progress view lists the flow's PRs in its order with `flow v<N>`
in its header.

## Seeing progress

This view is for the owner. The coordinator keeps to `status`, which is shorter.

```sh
$R plan $SCRATCH/inner-prs.json        # once: the run's inner PRs in order, so those not started are listed too (a flow replaces it)
open $SCRATCH/router/progress.html     # once: the page reloads itself every 15 s
$R progress                            # the same view as text, from any terminal with SCRATCH or ROUTER_STATE set
```

| The view shows | Read from |
|---|---|
| Every inner PR of the plan: done, running, at a gate, paused, stopped, not started | `plan.json`, and each chain's `state.json` |
| For each open PR: its steps, the step it is at, how long each step took, second attempts | the chain's state |
| What runs now: the worker's Orca title, its agent, model and effort, when it started, its last heartbeat and the phase it reported. A script step (a CI wait) is listed the same way | the chain's state, `liveness/` |
| What waits for the coordinator, since when, and whether it has picked the ring up | the chain's state, `wake/` |
| Questions nobody answered, workers outside a chain, the journal's last 12 lines | `events/`, `dispatches/`, `journal.md` |

The page is rewritten:

- by the mailbox daemon before every long-poll (`ROUTER_WAIT_MS`, two minutes unless set);
- by a chain's runner each time it is about to wait: before the next step starts, before a script, while its
  workers run, and when it ends. A saved state only marks the page as stale, so that nothing slow sits between a
  state and the call that state announces;
- by a coordinator command that changed a chain (`chain`, `resume`, `retry`, `fail`), as the command ends;
- when the doorbell shows a ring, after a reply, after an ad hoc worker starts, and after `plan`.

It asks Orca nothing. Three banners on it mean that someone has to act:

| Banner | Meaning | Do |
|---|---|---|
| "The mailbox daemon is not running" | It was stopped, and wrote that on the page as it went. | `router.py init` |
| "The router last rewrote this page N min ago" | No rewrite for three long-polls (at least 5 min): the daemon ended without a word. The page's own script shows this from its age. | `router.py status` |
| "A ring has waited N min" | The coordinator has not picked a ring up for 5 min, so its `router.py wait` is probably not running. | Wake the coordinator: `router.py last`, then `router.py wait` |

A plan is one JSON file:

```json
{"title": "Move the payments client to v3", "prs": [
  {"id": "p1", "title": "Pin the new client next to the old one", "part": "Part 1 · Dependencies"},
  {"id": "p2", "title": "Switch the checkout flow", "part": "Part 2 · Callers"}]}
```

`id` is the `<pr>` given to `router.py chain`; upper and lower case are the same. Rows with the same `part` are drawn
as one group, in the plan's order. Without a plan the view lists only the PRs whose chain was started. A chain the
plan does not name is listed last, as "not in the plan". A chain is "done" when its last step has passed; in
`router/templates/inner-pr.json` that is the merge gate. The router does not check that the merge happened.

`progress.py` only renders, and `router.py` catches whatever it raises: a mistake in the view does not stop a
daemon. The daemon's log then says `progress.html was not rewritten`.

## Every worker's logs

The report on a run has to say where its time and tokens went without anyone reading a transcript. Orca's archive
of a worker is bounded (`contentComplete` false, older messages clipped); the agent's own session file is the whole
log. So once a worker is released, the mailbox daemon starts `router/collector.py` for that dispatch in a process of
its own. It waits for the chain to record the attempt's end and checks, then writes, under the state directory only:

```
logs/<pr>/<step>/<dispatch>/       (an ad hoc worker: logs/<pr or _none>/_adhoc/<dispatch>/)
  meta.json        ids, attempt n, agent/model/effort (and what the start receipt said, when it differed), started,
                   ended, outcome, flow_version, cause, worktree, head_before..head_after, the check lines, the
                   release result, and where each file below came from
  orca-read.json   every `worker-read --source transcript` page, cursor followed until none or a page repeats;
                   read afresh once when Orca answers source_changed
  events/          the dispatch's events/*.json and its liveness file
  session.jsonl    the matched Claude Code or Codex session file, byte for byte (absent, and meta.json says why,
                   unless exactly one file matches)
  session.subagents/  the Claude session's subagents/ directory (each subagent's sidechain), copied whole
  tokens.json      input, output, cache_creation, cache_read, by model; turns; first and last timestamp. The
                   totals include the subagent files; "subagents" gives their share
logs/index.jsonl   one row per dispatch, for the report: ids, agent, model, effort, times, duration_s, outcome,
                   tokens (the totals), the session match, head range, checks_ok, Orca's contentComplete and clipping
```

| Session match | When |
|---|---|
| `unique` | Exactly one session file ran in the dispatch's worktree (Claude: the project directory named after it, and the `cwd` inside the file; Codex: `session_meta.cwd`) and overlaps [started − 2 min, ended + 2 min]. Of several, the only one that began within 3 min after the start. |
| `ambiguous` | Several, and not exactly one began within 3 min of the start: the paths are recorded, nothing is copied. |
| `none` | No such file, the worktree is unknown, or the agent is neither Claude nor Codex. |

A file whose lines are all sidechain lines is never a candidate, nor is a subagent file: a sidechain belongs to its
parent session and is kept with it (inline, or in the session's `subagents/` directory). Nothing is guessed. `index.jsonl` and `meta.json` hold ids, paths, timestamps and counts, never transcript
text; the copies stay in the state directory, never in a repository.

```sh
$R collect --all             # a run whose daemon was not collecting, or after a fix; a collected dispatch is skipped
$R collect --dispatch <id> --force   # collect one again
$R collect --dry-run         # the session match per dispatch and the counts; copies and writes nothing
$R status                    # "logs N/M settled dispatches collected"
```

A dispatch that fails to collect, for any reason, journals `collector: <dispatch>: <why>` and leaves no temporary
directory. One that another collector holds is counted `locked` in the summary and journaled once; a lock older
than 2 hours was left by a collector that died, and the next collect takes it over.

A collector that fails journals `collector: <dispatch>: <why>`; the daemon never waits for one. `ROUTER_COLLECT=0`
turns the automatic collection off; `FLOWS_CLAUDE_PROJECTS` (default `~/.claude/projects`) and
`FLOWS_CODEX_SESSIONS` (default `~/.codex/sessions`) say where the session files are. When Orca's archive answers
`archive_not_ready` (the code the fake uses: the real CLI's is not known yet) the first page is asked for again three
times, two seconds apart; then `orca-read.json` says `"status": "not available"`, never an empty transcript.

## What rings

| Event | Handled by | The coordinator sees |
|---|---|---|
| Heartbeat | mailbox daemon: last-seen time per worker | nothing |
| `worker_done`, succeeded, checks OK | daemon releases the terminal; runner starts the next step | nothing |
| `status` message | journal line | nothing |
| `worker_done`, failed | runner pauses | step, attempt, subject, summary, report path |
| A check that is not OK | runner pauses | every check line of that step |
| `worker-start` fails | runner pauses | Orca's message and the path of the kept receipt |
| `worker-start` gave no readable answer, or the runner ended during it | runner pauses as `start unknown`; nothing is started twice | the adopt and retry commands |
| A script step fails (CI red, no draft PR) | runner pauses | its last line |
| A gate (contract acceptance, merge) | runner pauses | the gate's title and its `show` lines. `resume` passes a gate only after it has rung |
| A step cannot be started (a variable has no value, a spec cannot be read) | runner pauses | `WAKE runner`, the reason, and `resume --set` |
| A worker's question | nothing pauses | the question, its options, the reply command |
| `escalation`, `merge_ready`, `handoff`, `decision_gate` | nothing pauses | subject, 600 characters of the body, the file with the rest |
| A live worker with no heartbeat for 20 min | nothing is stopped | which worker; `router.py workers` for Orca's verdict. If Orca shows it exited: `router.py fail` |
| `worker_done` from a worker the router did not start | not released | subject, summary, the release command |
| The mailbox check fails 5 times in a row (an error, or a lost connection) | daemon keeps trying | the error; no worker is touched |
| The mailbox daemon or a runner is dead | — | the doorbell exits 3 with a line starting `ACT:` |

Why no nudge is typed into the coordinator's terminal: Orca types "You have N orchestration messages" only when no
`check --wait` is waiting for that message's type (`mailbox-notification-coordinator.ts`, `notifyMessageArrived`,
Orca source `a781a602a8`). The daemon always waits, for every type. A message that arrives in the instant
between two waits can still produce a nudge; it needs no action.

## A chain definition

A JSON object with `steps`, run in order. An optional `kit` is a directory, relative to the definition file, that
holds the `specs/` and `checks/` the steps use (default: the definition's own directory). Both templates set
`"kit": ".."`, so they find `router/specs/` and `router/checks/`. A `kit` that is not a directory is refused when the
chain is created. A step's commands (`run`, `when`, `checks`, `show`) run in the kit directory, so `checks/x.sh` is
the kit's. Text fields are templates: `{NAME}` is replaced, and a name with no value
stops `router.py chain` before anything starts. In a command (`run`, `when`, `checks`, `show`) the value is
shell-quoted for you, so write `{TITLE}`, not `"{TITLE}"`. `${NAME}` is left to the shell.

| Step type | Fields | Meaning |
|---|---|---|
| worker (default) | `id`, `agent`, `model`, `effort`, `worktree`, `spec` (a file under the kit's `specs/`), `checks`, `readonly`, `group`, `when` | Starts one fresh Orca worker with the rendered spec. `readonly: true` requires HEAD and the uncommitted files of `{WT}` to be the same afterwards. Adjacent steps with the same `group` run at once. |
| `script` | `run`, `timeout` (s, default 600), `exports`, `when` | Runs a command. Exit 0 goes on. A line `VAR NAME=value` stores a variable for later steps (declare it in `exports`). |
| `gate` | `title`, `show` | Pauses for the coordinator. Each `show` command contributes its last line to the screen. |

`when` is a shell test; exit code other than 0 skips the step.

Values a template can use:

- given on the command line (`K=V`) or in the definition's `vars`;
- always: `PR`, `STATE`, `DEF_DIR` (the definition's directory), `KIT`, `CHECKS` (`{KIT}/checks`), and `SCRATCH`
  when it is exported;
- in a step: `STEP`, `ATTEMPT`, `HEAD_BEFORE`; in its checks also `TASK`, `DISPATCH`, `REPORT`, `HEAD_AFTER`.
  `HEAD_BEFORE` is where the step's first attempt started, and a retry keeps it: a commit that attempt 1 should not
  have made stays inside the range its checks look at, and a retry that reverts it passes. `resume` re-runs the
  checks with `HEAD_AFTER` at the tree as it is then;
- from earlier steps: `HEAD_BEFORE_<ID>` and `HEAD_AFTER_<ID>` (the id in upper case), and every exported `VAR`.

`router/templates/inner-pr.json` declares these (its `"variables"` says the same), and every variable of a
[profile](#repository-profiles):

| Variable | Default | Meaning |
|---|---|---|
| `WT` | none | The PR's worktree, on the PR's branch, cut from the PR's base commit. |
| `ISSUE` | none | The sub-issue whose "For the agent" comment is the PR's intent, kept at `$SCRATCH/intent/<number>.md`. |
| `BASE_BRANCH` | none | The integration branch the PR merges into (never `main`). A chain started without it is refused before anything runs, naming it. |
| `TITLE` | none | The PR title. |
| `RUN_CONTEXT` | empty | One or two sentences about this run that every worker should know, for example the upgrade or feature the PR belongs to. The contract and Codex review specs carry it. |
| `TESTS` | `1` | A blind test writer runs. Empty for a PR that only moves a dependency version: the contract then names every test the implementer writes or changes. |
| `LEDGER` | `1` | The PR has code to mutate. Empty for a PR with none. |
| `GREEN_PINNED` | empty | `1` when the PR's tests pin behaviour the old code already has, so the ledger runs a round 0 on the old code. |
| `IMPL_EFFORT` | `high` | The implementer's effort; `xhigh` for the hardest PR of a run. |

A retry is a new Orca task with the same spec plus the coordinator's note; it does not use `--retry-of`, because
the spec of an existing task cannot carry a note.

## Repository profiles

The specs name no test runner, linter, script or path of one repository. A profile carries those, as variables the
specs read: `router/profiles/<name>.json`, `{"name": …, "about": …, "vars": {…}}`. Every profile sets every
variable below, and `inner-pr.json` gives none of them a default, so a chain without a profile is refused before it
starts (`router.py chain … --dry-run` names `{TEST_CMD}` and the others). Where a gate does not apply, the profile
writes the literal `none` and the spec skips the gate and says so: skipping is a choice you can read in a file. A
value may contain `{SCRATCH}`, `{WT}` and `{PR}`, and no other placeholder.

| Variable | Meaning | `none` allowed | `python` | `typescript` | `bell` |
|---|---|---|---|---|---|
| `RULES` | A rules file every worker reads before anything else (standing rules, test limits). | yes: no rules file | `none` | `none` | `{SCRATCH}/briefs/rules-worker.md` |
| `TEST_CMD` | Starts a targeted test run; a spec appends paths or ids. Also used inside throwaway copies. | no | `python -m pytest -q` | `npm test --` | the limited pytest wrapper under a wall-clock bound, `PYTEST_PY={WT}/.venv/bin/python` |
| `UNIT_DIRS` | Where the fast tests live: the full unit suite (`{TEST_CMD} {UNIT_DIRS}`) and the validator's unit folders. | no | `tests` | `src` | `tests/unit` |
| `TEST_PATHS` | One git pathspec for every test file: what the implementer may only un-mark and what `/simplify` and the code fixer may not touch. A script step checks it. | no | `tests/` | `:(glob)**/*.test.ts` | `tests/` |
| `TEST_CONFIG` | One path: the test runner's settings, which no worker after the contract changes. A script step checks it. | no | `pytest.ini` | `vitest.config.ts` | `pytest.ini` |
| `COPY_SETUP` | Run inside a throwaway copy of the worktree (ledgers, test fixer, validator replay) before its first test run, so the tests there find the installed dependencies. | yes: the copy needs nothing | `none` | `ln -s {WT}/node_modules node_modules` | `none` |
| `LINT_CMD` | The lint and type-check gate, run from the worktree root with the shell variable `BASE` set to the PR's base commit, so it can pick the changed files. A pipeline starts with `set -o pipefail;`, so a failing `git diff` fails the gate. | yes: the validator reports "skipped: no lint command" | ruff on the changed `*.py` | `tsc --noEmit`, then eslint on the changed `*.ts`/`*.tsx` | `{SCRATCH}/bin/ci-lint-changed.sh "$BASE"` |
| `FROZEN_PATHS` | Paths whose diff must be empty unless the contract says otherwise (goldens, snapshots, lockfiles); several, separated by spaces. | yes: "skipped: no frozen paths" | `none` | `none` | `tests/integration/bot/snapshots/` |
| `EXTRA_SUITE` | The command of one extra suite the validator runs as its own run (a contract or ABI suite). | yes: "skipped: no extra suite" | `none` | `none` | the ABI suite through the same wrapper, `-n 0 -q -rfE --tb=line -p no:cacheprovider` |
| `COMMIT_CMD` | How a worker commits; it takes `git commit`'s arguments. Hooks always run. | no | `git commit` | `git commit` | `{SCRATCH}/bin/locked-commit.sh` |

`UNIT_DIRS` and `TEST_PATHS` differ on purpose: `UNIT_DIRS` is where the fast tests are run from, `TEST_PATHS` is
what is guarded. In a Python repository with slow tests under `tests/integration`, `UNIT_DIRS` is `tests/unit` and
`TEST_PATHS` is still `tests/`. `TEST_PATHS` and `TEST_CONFIG` are each one word, because the router quotes a value
as one shell word in a check.

Pytest-only today: the blind test writer marks a red-first test `@pytest.mark.xfail(strict=True, …)`, and
`checks/xfail-only.py` (the implementer's check after a test writer, on the `tests/` prefix) accepts only the
removal of those markers. A chain for another test runner sets `TESTS=` (empty): the test writer is skipped, the
xfail check says `OK xfail-only: no test writer on this PR`, and the contract names every test the implementer
writes. The `typescript` profile is meant to be run that way.

A [flow file](#the-flow-file) names the profile in its `profile` field and merges its `vars` into every PR's
variables. A chain started by hand, without a flow, gets them as `K=V`. Each pair is read into an array,
NUL-separated, so a value with spaces or quotes stays one argument (bash and zsh):

```sh
P=$FLOWS/router/profiles/python.json
args=(); while IFS= read -r -d '' kv; do args+=("$kv"); done < <(python3 -c 'import json,sys; sys.stdout.write("".join(f"{k}={v}\0" for k, v in json.load(open(sys.argv[1]))["vars"].items()))' "$P")
$R chain <pr> --def $FLOWS/router/templates/inner-pr.json WT=… ISSUE=… BASE_BRANCH=… TITLE=… "${args[@]}"
```

On the command line a value is taken as it is: a `{SCRATCH}` or `{WT}` inside it reaches the worker unfilled. The
`python` profile has none; for `typescript` and `bell` write the paths out, or use a flow file, which fills a profile
value's `{SCRATCH}`, `{WT}` and `{PR}` before the specs are rendered.

A new repository gets its own profile: copy the closest one, set every variable, and run
`python3 tests/test_router.py -k Profiles` (it checks that each profile sets every variable and renders every spec).

## What the run provides

The specs read these paths under the run directory (`grep -rhoE '\{SCRATCH\}/[A-Za-z0-9_./-]+' router/specs
router/templates | sort -u`, plus the profile values):

| Path | Who writes it | Before the first chain |
|---|---|---|
| `{SCRATCH}/intent/<ISSUE>.md` | the coordinator: the sub-issue's "For the agent" comment, one file per sub-issue | create it for every PR's `ISSUE` |
| `{SCRATCH}/plan/` | the coordinator: the run's plan, which the intent overrides | optional |
| `{SCRATCH}/contracts/`, `ledger/`, `reviews/`, `pr/`, `work/` | the chain's workers (contract, ledger, reviews, PR body, throwaway copies) | nothing: workers create them |
| the `RULES` file (`bell`: `{SCRATCH}/briefs/rules-worker.md`, until M1b hardcoded in every spec) | the coordinator | create it when `RULES` is not `none` |
| the scripts `LINT_CMD`, `TEST_CMD`, `EXTRA_SUITE` and `COMMIT_CMD` name (`bell`: `{SCRATCH}/bin/ci-lint-changed.sh`, `locked-commit.sh`, `pytest-limited.sh`, `bounded.sh`; the lint script was hardcoded in the validator spec) | the coordinator, copied from the repository's tooling | create each one the profile names |

`router/templates/inner-pr.json`'s `"files"` lists what each step writes.

## Checks

| Script | OK when |
|---|---|
| `router/checks/xfail-only.py <wt> <from> <to> [prefix…]` | Under `tests/`, the only change is removed xfail markers and imports the removal left unused. An added, deleted or non-Python test file, a changed body, a new or loosened marker, a new or changed import (module, name, alias or relative level), or an uncommitted edit is NOT OK. It does not see `pytest.ini`: guard that with the next check. |
| `router/checks/files-untouched.sh <wt> <from> <to> <pathspec…>` or `… --changed-in <a> <b>` | None of the named files changed between the two commits, and none has an uncommitted change. |
| `router/checks/pr-head.sh <wt> <pr>` | The PR's head commit is the worktree's HEAD and no tracked file is uncommitted. Without it, a commit that was never pushed gets "OK ci" for the commit before it. Read-only. |
| `router/checks/head-unmoved.sh <wt> <sha>` | HEAD is still that commit. |
| `router/checks/contract-sha.sh record <file>` / `check <file> <sha>` | `record` prints `VAR CONTRACT_SHA=…`; `check` is OK when the file still has that sha256. |
| `router/checks/verdict-line.sh <file> [pattern]` | The file's first `VERDICT:` line matches (default `PASS`, `OK` or `ACCEPT`). |
| `router/checks/file-exists.sh <path> [min lines]` | The file exists with at least that many lines. |
| `router/checks/ci-green.sh [--watch] <pr>` | No check of the PR failed, was cancelled or is pending. Read-only. |

Every script prints its header with `--help` and exits 2 on bad usage.

## After a restart

- **The coordinator's session restarted or was compacted:** `router.py status`, then `router.py wait` in the
  background. The daemons do not belong to the session and keep running.
- **A new terminal takes the Run over:** `router.py init --run <run_id>`. It binds the terminal, restarts the
  mailbox daemon for it, and running chains go on (they read `run.json` at every Orca call).
- **The machine restarted:** `router.py init`, then `router.py resume <pr>` for each chain `status` shows as
  `RUNNER GONE`. A step that was running is picked up where it was; its worker is not started twice.
- **A delivery handled but not acknowledged** (the daemon died in between) is replayed by Orca and skipped by the
  daemon: an event file per message id is the record.
- **A runner that died at a gate before the ring:** `resume` does not pass that gate. It says so, and the gate
  rings.

## Tested how

2026-10-09, the collector, review round 1 (M3).

- `cd plugins/flows && python3 tests/test_router.py`: 145 tests, green (137 before). The 8 new ones, in
  `Collector`: a page that repeats under a new cursor ends the read; `source_changed` starts the read afresh once,
  and a second one is recorded; `tries` counts the calls made; a malformed chain state is journaled, by `--all` and
  by the daemon's `--wait-settled`, while the other dispatch is collected; a held lock is reported and journaled
  once, and taken over after 2 hours; a failed collection leaves no temporary directory; a step's attempt records
  agent, model, effort, worktree and what Orca launched when it differs; an ad hoc worker started with
  `--worktree current` beside the coordinator's own session matches `unique` under `_adhoc/`. The session test
  now also copies the fixture's subagent files and sums them by hand, and has a Codex rollout from another `cwd`
  that overlaps the window.
- Mutants, each in a scratch copy against `Collector`: no repeated-page stop, no Codex `cwd` check, the ad hoc
  start recorded after worker-start, subagent usage not summed, and no temporary-directory cleanup. Each one fails
  a named test.

2026-10-09, the collector (M3).

- `cd plugins/flows && python3 tests/test_router.py`: 137 tests, green (127 before). The 10 new ones are the class
  `Collector`: a 3-page Orca archive kept whole and in order, and a cursor that names itself ends the read;
  `archive_not_ready` asked again 3 times, then "not available"; the overlapping Claude file copied byte for byte
  (the decoy two hours earlier, a sidechain-only file and a file with another `cwd` are no candidates) and its
  tokens equal to the sums worked out by hand from the fixture, a Codex rollout with and without token counts; two
  candidates ambiguous with nothing copied, no candidate, no worktree, an unsupported agent, and a second
  candidate that began late leaving the match unique; collecting twice, and again with `--force`, gives the same
  bytes; no sentinel from the fixture transcripts in `index.jsonl` or any `meta.json`; `--dry-run` writes
  nothing; and on the daemon: a released worker collected once with its check lines, a failing collector
  journaled while the second worker still settles, and `ROUTER_COLLECT=0`.
- The matcher over a real run's records (the group-B run, read-only, `--dry-run`): 258 settled dispatches, 152
  unique, 10 ambiguous, 96 none, 8 not settled. All 96 are ad hoc workers started with `--worktree current`, whose
  worktree the router did not record before this change; it records it now.

2026-10-09, the flow file (M1a), review round 1.

- `cd plugins/flows && python3 tests/test_router.py`: 127 tests, green (121 before). The 6 new ones:
  `FlowVersions.test_a_reorder_of_the_prs_is_a_new_version`; `FlowScheduler`: a value that names a declared `{WT}`
  nobody gives waits for it, and a runner whose chain completes starts the next PR itself (the daemon's long-poll
  set to 30 s); `FlowReread`: the worktree and the kit of a settled worker step cannot change, and the runner waits
  for `flow.lock` at a step boundary; `FlowCompat`: a profile's `{SCRATCH}`, `{WT}` and `{PR}` are filled by the flow
  (`bell.json`, the rendered `TEST_CMD` and `RULES` in the chain's variables). `test_a_value_that_names_an_unknown_variable`
  also checks that a profile's and a template's bad value name their layer.
- 8 mutations, each caught by the test written for it: values left unfilled, no scheduler pass from a finishing
  runner, no `WT`/`KIT` for a worker step, no wait for a declared name inside a value, a runner boundary without
  `flow.lock`, no order comparison, and two that drop the layer from a problem line.

2026-10-09, the flow file (M1a).

- `cd plugins/flows && python3 tests/test_router.py`: 109 tests on the M1a branch, green (76 before); 121 once
  merged with the profiles (their 11, and `FlowCompat.test_a_flow_of_inner_prs_takes_its_variables_from_a_profile`:
  a flow of `inner-pr.json` PRs waits for the profile's variables until its `profile` field names `python.json`).
  The new ones are in the classes `FlowValidation` (each refusal of the file, with its reason line; a PR missing a
  variable is accepted and waits), `FlowVersions` (v1, a no-op apply, a stale `--base` that leaves the copy byte for
  byte, history and journal, the change lines), `FlowScheduler` (after, slots, manual, a PR that waits for `WT`,
  three scheduler passes that race start one PR once, a flow the daemon cannot read), `FlowReread` (an edited, an
  added and a removed pending step; a running, a done and an about-to-start step refused with `state.json` and the
  copy unchanged byte for byte; variables a settled step used; exports kept; the cause of each attempt; a flow the
  runner cannot read), `FlowRemoval`, `FlowCompat` (`chain`, `plan` and `progress` with and without a flow), and one
  in `Templates`: a definition that names a kit runs its `run`, `when`, `checks` and `show` lines from the kit.
- 47 hand-made mutations of the new code, one at a time, each against the test written to catch it: 47 caught. One
  survived the first pass, and it showed dead code: `retry` marked its step's cause `retry`, which a later attempt
  already defaults to. The mark is gone; the mutation of the default is caught. A test written for the pass found one
  bug, now fixed: when the flow also set a variable that a step exported, the re-read gave the flow's value back.
- Not tested: a flow on real Orca, and two applies from two terminals at the same instant (they take turns under
  `flow.lock`, which the race test covers for scheduler passes).

2026-10-09, repository profiles.

- `cd plugins/flows && python3 tests/test_router.py`: 87 tests, green (76 before). The 11 new ones: the class
  `Profiles` (9: each profile sets every variable with no placeholder but `{SCRATCH}`, `{WT}`, `{PR}`; the template
  defaults none of them; every spec of both templates renders with each profile and leaves no placeholder; the
  validator and the implementer carry the profile's commands; a gate set to `none` is skipped, never run, and no
  spec tells a worker to run, read or edit a variable set to `none`; the template's checks guard the profile's test
  files; the rendered validator and implementer match `tests/fixtures/profiles/<profile>/`; a chain without a
  profile is refused naming `{TEST_CMD}`, and passes with the `python` profile's vars), and two `Sweep` tests that
  keep test runners, linters, a repository's scripts and paths out of `router/specs/` and `router/templates/`. A
  wording change in the validator or implementer is a diff in those snapshots: `FLOWS_UPDATE_SNAPSHOTS=1 python3
  tests/test_router.py -k snapshots` rewrites them.

2026-10-09, the move into this plugin.

- `cd plugins/flows && python3 tests/test_router.py`: 76 tests, green. The 5 new ones: an `inner-pr.json` chain
  without `BASE_BRANCH` is refused and starts no worker; a definition in a subdirectory finds its kit; a sweep that
  keeps project names, version literals and past PR names out of `router/specs/` and `router/templates/` (and a check
  of its pattern); the quoting test run under a 120-character `TMPDIR`.
- `router.py chain <pr> --def router/templates/inner-pr.json --dry-run` without `BASE_BRANCH` exits 1 and names
  `{BASE_BRANCH}`; with it, `OK … 17 steps`.

The entries below predate the move: `chain-inner-pr.json` and `chain-smoke.json` are today's
`router/templates/inner-pr.json` and `router/templates/smoke.json`.

2026-10-04, on Orca 1.4.219.

- `python3 tests/test_router.py`: 52 tests, about 110 s, against `tests/fake-orca`, which answers with the JSON
  shapes Orca printed that day. They cover every row of "What rings", a parallel group, skipped steps, retry and
  resume, a replayed delivery, a killed runner (also one killed during `worker-start`, and one killed at a gate)
  and a killed daemon, the chain limit, and each check script on a throwaway git repository. `draft-pr.sh` and
  `pr-head.sh` run against a stand-in `gh`.
- An independent review found 11 defects after the first live run (a retry that moved a check's range, a gate
  that `resume` passed unseen, a dead worker with no way out, an `--accept` that ended in a crash loop, an import
  swap that `xfail-only.py` accepted, and smaller ones). Each is fixed and has a test in the class `Review` or
  `Checks`; the 13 router tests written for them fail on the router as it was before.
- 36 mutations of the scripts, one at a time, each run against the test that should catch it: 36 caught.
- Live, Run `run_0bd539a0200b`, `chain-smoke.json`, 6 worker starts on Sonnet 5.5.
- `checks/ci-green.sh 3973` on the real PR printed `OK ci: 9 pass, 6 skipping`.

2026-10-05, the owner's view (`progress`, `plan`, `progress.html`), against the stand-in only.

- `python3 tests/test_router.py`: 71 tests, about 140 s, green on Python 3.14.6 and on 3.9.6. Of the 19 new ones,
  13 are in the class `Progress` (the text and the page of a run on the stand-in) and 5 in `ProgressView`
  (`progress.py` alone).
- 54 mutations of `router.py` and `progress.py`, one at a time, against those tests: 54 caught. One survived the
  first pass (the runner's last rewrite moved after its pid file); `test_a_stopped_chain_is_on_the_page_when_stop_returns`
  was written for it. The unchanged kit passed all 8 control runs made between the mutations.
- The first design rewrote the page inside every state save. That put about 80 ms between a saved state and the
  call it announces, and two older tests failed on it. A save now only marks the page stale.
- Python 3.9 had been claimed and never run. Its argparse refused `chain <pr> --def <file> K=V`, also on the router
  as it was before this change. `main()` now takes the left-over words itself, and refuses a word no command takes.
- The page was opened in Chromium at 1400 px (light) and 900 px (dark), from a state made with the stand-in.

2026-10-05, the role specs.

- `router.py chain <pr> --def chain-inner-pr.json --dry-run` prints `OK … 17 steps` for a full PR (`GREEN_PINNED=1`) and for
  the pilot (`TESTS= LEDGER=`), and names `{ISSUE}` when it is not given. `tests/test_router.py`: 71 tests, green.
- The chain gained one variable, `ISSUE` (the sub-issue whose "For the agent" comment is the PR's intent, kept at
  `$SCRATCH/intent/<number>.md`), a read-only contract step, and a verdict file for each fixer
  (`fix-tests-done.md`, `fix-code-done.md`) that the fixer step and the merge gate check.

## Not tested, and limits

- No flow has run on real Orca. The page that edits a flow (M2) and creating the worktrees a flow names are not
  part of the flow file yet.
- The collector has not read a real Orca archive: `worker-read` is answered by the fake, in the shape documented
  above, and its `source_changed` restart is taken from `worker-read --help`. Codex reports no cache writes, and
  older Codex clients no token counts: those fields say "not recorded". No token prices: the report (M4) does that.
- The progress page has never shown a real Orca run.
- The view is local: a file and a command. Nothing is posted on the epic and nobody is notified.
- `stop <pr>` does not end a script step: the runner finishes the script first (a CI wait can take 90 minutes),
  and `stop` says `STILL RUNNING` after 15 s. The page shows the script as running until then.

- `draft-pr.sh` has never opened a real PR, and `router/templates/inner-pr.json` has never run. Its role specs were
  written on 2026-10-05 and no worker has run one. No profile has driven a real chain.
- Codex as a worker under the router, a worker in another worktree (`path:` or `new-child`), and a run longer
  than a few minutes are untested.
- A reboot is untested; the recovery above is by construction (state on disk) and by the killed-process tests.
- The router does not cap Claude workers. Two chains at once (`ROUTER_MAX_CHAINS`) give at most two Claude
  workers plus ad hoc ones. A chain holds its slot until it is done, also while it waits at a gate. The router does not cap test runs
  either: a profile's `TEST_CMD` and `COMMIT_CMD` can (the `bell` profile's wrapper lets one test run at a time across all workers).
- Bounded loops (a second review round, a survivor going back to a test writer) are not steps. They are the
  coordinator's decisions: an ad hoc worker, then `resume`.
