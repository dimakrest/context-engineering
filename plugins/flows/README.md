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
[Seeing progress](#seeing-progress) · [What rings](#what-rings) ·
[A chain definition](#a-chain-definition) · [Repository profiles](#repository-profiles) ·
[What the run provides](#what-the-run-provides) · [Checks](#checks) · [After a restart](#after-a-restart) ·
[Tested how](#tested-how) · [Not tested, and limits](#not-tested-and-limits)

## Parts

| File | What it is |
|---|---|
| `router/router.py` | The mailbox daemon, the chain runner, the doorbell and the coordinator's commands. `router.py --help` prints all of it. Python 3.9+, standard library only. |
| `router/progress.py` | Renders the owner's view: the text of `router.py progress` and `progress.html`. It reads nothing itself. |
| `router/templates/inner-pr.json` | One inner PR of a larger change as a chain: 17 steps, two gates (contract acceptance, merge). `--dry-run` lists the steps and names anything missing. |
| `router/specs/*.md`, `router/contract-template.md` | The 12 role specs the chain names, one per worker step, in Orca's shape (target, change, constraints, ownership, observable acceptance); `arbiter.md`, a template the coordinator fills for a test dispute; and the template every contract follows. |
| `router/profiles/*.json` | What differs between repositories (test, lint and commit commands, guarded paths, the rules file): `python`, `typescript`, `bell`. See [Repository profiles](#repository-profiles). |
| `router/templates/smoke.json`, `router/specs/smoke-*.md` | A harmless chain that proves the router against real Orca: two workers at once, a question, a failure with a retry, a script step, a gate. |
| `router/checks/*` | Checks that print one line, `OK …` or `NOT OK …`, and exit 0 or 1. |
| `router/draft-pr.sh` | Opens the inner PR as a draft, so that step needs no model. Refuses `main` as the base. |
| `tests/` | `test_router.py` (85 tests) and `fake-orca`, a stand-in for the Orca CLI. |
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

## Seeing progress

This view is for the owner. The coordinator keeps to `status`, which is shorter.

```sh
$R plan $SCRATCH/inner-prs.json        # once: the run's inner PRs in order, so those not started are listed too
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
chain is created. Text fields are templates: `{NAME}` is replaced, and a name with no value
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
| `LINT_CMD` | The lint and type-check gate, run from the worktree root with the shell variable `BASE` set to the PR's base commit, so it can pick the changed files. | yes: the validator reports "skipped: no lint command" | ruff on the changed `*.py` | `tsc --noEmit`, then eslint on the changed `*.ts`/`*.tsx` | `{SCRATCH}/bin/ci-lint-changed.sh "$BASE"` |
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

Until the flow file (milestone M1a) merges a profile into a chain's variables, give its `vars` as `K=V`. Each pair
is read into an array, NUL-separated, so a value with spaces or quotes stays one argument (bash and zsh):

```sh
P=$FLOWS/router/profiles/python.json
args=(); while IFS= read -r -d '' kv; do args+=("$kv"); done < <(python3 -c 'import json,sys; sys.stdout.write("".join(f"{k}={v}\0" for k, v in json.load(open(sys.argv[1]))["vars"].items()))' "$P")
$R chain <pr> --def $FLOWS/router/templates/inner-pr.json WT=… ISSUE=… BASE_BRANCH=… TITLE=… "${args[@]}"
```

On the command line a value is taken as it is: a `{SCRATCH}` or `{WT}` inside it reaches the worker unfilled. The
`python` profile has none; for `typescript` and `bell` write the paths out, or wait for the flow file, whose merge
must fill a profile value's `{SCRATCH}`, `{WT}` and `{PR}` before the specs are rendered.

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

2026-10-09, repository profiles.

- `cd plugins/flows && python3 tests/test_router.py`: 85 tests, green (76 before). The 9 new ones: the class
  `Profiles` (7: each profile sets every variable with no placeholder but `{SCRATCH}`, `{WT}`, `{PR}`; the template
  defaults none of them; every spec of both templates renders with each profile and leaves no placeholder; the
  validator and the implementer carry the profile's commands; a gate set to `none` is skipped, never run; the rendered
  validator and implementer match `tests/fixtures/profiles/<profile>/`; a chain without a profile is refused naming
  `{TEST_CMD}`, and passes with the `python` profile's vars), and two `Sweep` tests that keep test runners,
  linters, a repository's scripts and paths out of `router/specs/` and `router/templates/`. A wording change in the
  validator or implementer is a diff in those snapshots: `FLOWS_UPDATE_SNAPSHOTS=1 python3 tests/test_router.py -k
  snapshots` rewrites them.

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
