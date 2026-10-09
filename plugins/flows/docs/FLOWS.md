# Flows — a run of inner PRs on Orca, without a model in the loop

A **flow** is a run of inner pull requests carried through Orca workers by the **router**: scripts that sit
between an Orca Run's mailbox and the coordinator. The router releases finished workers, starts each PR's next
step, runs the step's checks and collects every worker's logs. No model runs in it. The coordinator is rung only
when something needs a decision: a gate, a failure, a check that is not OK, a worker's question.

---

**New to this? Start with the walkthrough:** `docs/FLOWS_GETTING_STARTED.html`. Open it in a browser. It runs
the smoke flow on real Orca, one command at a time. This page is the reference.

---

Contents: [The idea](#the-idea-in-three-sentences) · [When to use it](#when-to-use-it) ·
[Quick start](#quick-start) · [The state directory](#the-state-directory) · [The flow file](#the-flow-file) ·
[The roles and the specs](#the-roles-and-the-specs) · [Profiles](#profiles) · [The page](#the-page) ·
[The collector](#the-collector) · [The report](#the-report) · [A chain definition](#a-chain-definition) ·
[Checks](#checks) · [What the run provides](#what-the-run-provides) · [What rings](#what-rings) ·
[The coordinator's loop](#the-coordinators-loop) · [After a restart](#after-a-restart) ·
[Environment variables](#environment-variables) · [Troubleshooting](#troubleshooting) ·
[Limits](#current-status-and-limits) · [Tested how](#tested-how-history) · [Related](#related)

## The idea in three sentences

In a run without the router, every `worker_done` woke the coordinator, which then read it, released the
terminal, acknowledged it, wrote a journal line and started the next worker by hand: 102 times in one run. Those
steps need no judgement, so the router does them from files on disk, and survives a compaction because it never
lived in the session. What is left for the coordinator is the decisions, and what is left for the owner is one
versioned file, the flow, that says which PRs run, in what order, with which steps.

## When to use it

| Use a flow | Use something else |
|---|---|
| Several PRs of one change, each through the same chain (contract, tests, implementation, validation, reviews, CI) | One PR you can drive in a session |
| The run outlives a context window, or runs while you sleep | A change you will review in five minutes |
| You want to know afterwards where the time, the tokens and the interruptions went | Nobody will read the numbers |
| You are in Orca, with its orchestration CLI | No Orca: the router speaks only to `orca orchestration` |

A mission (the `missions` plugin of the same marketplace, and its guide MISSIONS.md) and a flow answer different questions. A mission pins down what
"done" means before the code and grades it blind. A flow carries many PRs through a fixed chain of roles on Orca
workers. A flow's PR can follow a mission's contract.

## Quick start

From the coordinator's Orca terminal (the router reads `ORCA_TERMINAL_HANDLE` and `ORCA_PANE_KEY` from it):

```sh
R=${CLAUDE_PLUGIN_ROOT}/router/router.py                      # in a checkout: plugins/flows/router/router.py
export SCRATCH=$HOME/.cache/my-run ROUTER_STATE=$HOME/.cache/my-run/state
orca orchestration run-create --objective "<the run's goal>" --json   # a Run of your own: its mailbox is the router's
python3 $R init                                               # records the Run, starts the mailbox daemon
python3 $R flow apply flow.json --dry-run                     # the change lines, or NOT OK and one line per problem
python3 $R flow apply flow.json --by me --note "first plan"   # v1; under start auto every ready PR starts
python3 $R page --open                                        # the live page in an Orca browser tab
python3 $R wait                                               # in the background, the last action of every turn
```

Then answer each ring (below), and when the chains are done, `python3 $R report`. The skills walk the same steps:

| Skill | Does |
|---|---|
| `/flows:flow-plan` | writes the flow file with the owner, and loops on `flow apply --dry-run` until it validates. Starts nothing |
| `/flows:flow-run` | the coordinator's loop: bind, apply, page, wait, rule on each ring |
| `/flows:flow-status` | where a run is: status, the flow's PRs, the page's URL, logs collected of settled |
| `/flows:flow-report` | collects the logs if needed, builds the report, opens it, reads the lessons back |

## The state directory

`$ROUTER_STATE`, default `$SCRATCH/router`, holds everything. It is never inside a repository, never committed.

```
$ROUTER_STATE/
  run.json                 the Run and the coordinator terminal the daemons act for
  mailbox.pid, mailbox.log the mailbox daemon
  page.json                where the daemon serves the page: url, host, port, pid (gone when it stops)
  flow.json, flow.lock     the router's copy of the flow (every version's history); the lock applies take turns at
  chains/<pr>/state.json   one PR's chain: its steps, their attempts, the variables (def.json: the PR's own steps)
  dispatches/<id>.json     which chain and step started each worker
  events/<id>/*.json       every message that is not a heartbeat
  liveness/<id>            a worker's last heartbeat: time and phase
  wake/*.txt, wake/seen/   what the doorbell prints, then what it has printed
  journal.md               one line per settled event, append-only
  logs/                    each settled worker's logs and session file (the collector), logs/index.jsonl
  progress.html            the owner's view as a file that reloads itself
  report.html              the run report, written by router.py report only
```

`router.py --help` lists the same files.

## The flow file

One JSON file plans the run. `router.py flow apply` is the only way it enters or changes a run, and every
accepted apply is a new version with one history row. Each PR's runner follows the latest version for the steps it
has not started, and never touches work in flight.

```json
{
  "title": "Move the payments client to v3",
  "slots": 2,
  "start": "auto",
  "templates": {"inner-pr": "/abs/path/to/flows/router/templates/inner-pr.json"},
  "profile": "/abs/path/to/flows/router/profiles/python.json",
  "vars": {"RUN_CONTEXT": "The payments client moves from v2 to v3.", "WT": "{SCRATCH}/wt/{PR}"},
  "prs": [
    {"id": "A1", "part": "Part 1 · Dependencies", "title": "Pin the v3 client", "base": "payments-v3", "after": [],
     "template": "inner-pr", "vars": {"ISSUE": "41", "TITLE": "A1: pin the v3 client", "TESTS": "", "LEDGER": ""}},
    {"id": "B1", "part": "Part 2 · Callers", "title": "Switch checkout to v3", "base": "payments-v3", "after": ["A1"],
     "template": "inner-pr", "vars": {"ISSUE": "42", "TITLE": "B1: switch checkout to v3", "IMPL_EFFORT": "xhigh"}}
  ]
}
```

| Field | Meaning |
|---|---|
| `title` | shown by `flow show`, the page and the report |
| `slots` | chains open at once (default `ROUTER_MAX_CHAINS`, 2). A chain holds its slot through its gates |
| `start` | `auto`: the router starts a PR as soon as it is ready. `manual`: only `router.py flow start <pr>` does |
| `templates` | name → chain definition file, relative to the flow file or absolute |
| `profile` | a [profile](#profiles), relative or absolute; its `vars` are the lowest layer |
| `vars` | variables for every PR |
| `prs[].id` | letters, digits, `.`, `_`, `-`, starting with a letter or digit; upper and lower case are the same |
| `prs[].part`, `title` | the group the PR is drawn in, and its line |
| `prs[].base` | the branch it merges into; it becomes `BASE_BRANCH`. Never `main` or `master` |
| `prs[].after` | ids whose chain must be done before this one starts |
| `prs[].template` | a name from `templates` |
| `prs[].vars` | the PR's own variables |
| `prs[].steps` | optional: the PR's whole step list, replacing the template's (the template still gives the kit and its `vars`) |

The order of `prs` is part of the version: ready PRs start in it, and an apply that only reorders them is a new
version. `/flows:flow-plan` writes this file with the owner.

### The precedence of variables

Lowest first; a later layer wins:

1. the profile's `vars` (their `{SCRATCH}`, `{WT}` and `{PR}` filled in);
2. the template's `vars`;
3. the flow's `vars`;
4. the PR's `vars`;
5. the router's own, which no `vars` may set: `PR`, `STATE`, `DEF_DIR`, `KIT`, `CHECKS`, and `BASE_BRANCH` from
   `base`.

`SCRATCH` from the environment of `flow apply` fills in when no layer sets it. A value may name another variable
(`"WT": "{SCRATCH}/wt/{PR}"`). A name that the template declares and nobody gives makes the PR wait: `flow show`
says `waiting for: WT`. A name nobody declares is refused as a typo, and the line names its layer: `vars.X`,
`A1: vars.X`, `template inner-pr: vars.X`, `profile python: vars.X`.

### What apply refuses

A refusal prints `NOT OK flow: <n> problem(s), nothing changed` and one line per problem, exits 1, and writes no
version, no history row and no journal line. `--dry-run` runs the same checks and writes nothing.

| In the file | Against the run |
|---|---|
| an unknown template name; a template or profile that is not a file | `--base <v>` that is not the current version: `stale: the run is at v<N>` (the page always sends it) |
| a cycle in `after` (`after: a cycle: A1 -> B1 -> A1`); an `after` that names no PR | a settled step (done, running, at a gate, or about to start) edited, removed or moved, or a step put before it |
| one id twice; an id `chain` would refuse | a changed value of a variable a settled step used |
| `base` of `main` or `master` | a changed `after` of a PR whose chain exists, or that PR removed |
| a value that names an unknown variable | a PR whose chain was started with `router.py chain` |
| a step list the chain validator rejects | |
| `slots` not a positive integer; `start` not `auto` or `manual`; `vars` that set a variable the router sets | |

A pending step may be edited, added, removed or reordered freely.

### What runs

- **The scheduler.** Under `auto`, every ready PR starts in the flow's order. Ready: no chain yet, every PR it is
  `after` done, a slot free, a value for every variable. The journal says `flow: started <pr> (v<N>)`. The mailbox
  daemon runs a pass once per long-poll, so does `flow apply` and a runner whose chain just completed.
- **The runner, at every step boundary,** re-reads the flow: its settled steps stay as they are, the rest follow the
  latest version. The journal says what changed (`flow v3: step edited: model … -> …`). Each attempt records the
  `flow_version` it ran under and its `cause` (`first`, `retry`, `resume_from`).
- **A flow that cannot be read** pauses the runner with `WAKE runner · <pr>: the flow could not be read: <why>`.

| Command | Prints |
|---|---|
| `router.py flow apply <file> [--base <v>] [--by <who>] [--note "<text>"] [--dry-run]` | `OK flow v<N>: <n> changes` and the change lines, then any PR it started; or the refusal |
| `router.py flow show` | `flow v<N> · slots <used>/<n> · start <mode>`, then one line per PR with its state or what it waits for |
| `router.py flow start <pr>` | `OK started <pr> (v<N>), runner pid …`, or why it is not ready |
| `router.py flow history [<n>]` | the last n versions: `v<N> · <when> · by <who> · <n> changes · <note>`, then the changes |

### Without a flow

`router.py chain <pr> --def <template> K=V …` still starts one PR by hand, and `--dry-run` lists its steps and names
any variable with no value. An old `router.py plan <plan.json>` still lists PRs that have not started. With a flow,
`chain` refuses the PRs the flow names and `plan` is refused: the flow is the plan.

## The roles and the specs

A chain definition is a list of steps: a **worker** step starts one fresh Orca worker with a rendered spec, a
**script** step runs a command (a line `VAR NAME=value` sets a variable for later steps), a **gate** pauses for the
coordinator. Adjacent worker steps with the same `group` run at once. `router/templates/inner-pr.json`:

| Step | Kind | Spec (`router/specs/`) | Who | Its checks guard |
|---|---|---|---|---|
| `contract` | worker, read-only | `contract.md` | Fable, xhigh | the contract exists, 10 lines or more |
| `accept` | gate | | the coordinator | contract acceptance |
| `freeze` | script | | | records the contract's sha256 |
| `tests` | worker | `test-writer.md` | Opus | the contract unchanged (skipped when `TESTS` is empty) |
| `ledger_r0` | worker, read-only | `ledger-r0.md` | Codex | its verdict line (only when `GREEN_PINNED`) |
| `implement` | worker | `implementer.md` | Opus, `IMPL_EFFORT` | only xfail markers removed under `tests/`; `TEST_CONFIG` and the contract unchanged |
| `ledger_r1` | worker, read-only | `ledger-r1.md` | Codex | its verdict line (only when `LEDGER`) |
| `validator` | worker, read-only | `validator.md` | Sonnet | its verdict line and the PR body |
| `draft_pr` | script | | | `router/draft-pr.sh` opens the draft PR; refuses `main` |
| `simplify` | worker | `simplify.md` | Opus | `TEST_PATHS` and `TEST_CONFIG` untouched; the PR's head is the worktree's |
| `review_codex`, `review_claude` | workers, read-only, one group | `review-codex.md`, `review-claude.md` | Codex; Opus xhigh | each review file exists |
| `triage` | worker, read-only | `triage.md` | Fable | `VERDICT: TRIAGED` |
| `fix_tests`, `fix_code` | workers, each only when triage gave it work | `fixer-tests.md`, `fixer-code.md` | Opus | its verdict file; `fix_code` leaves the tests untouched |
| `ci` | script | | | `router/checks/pr-head.sh`, then `router/checks/ci-green.sh --watch` |
| `merge` | gate | | the owner | ready to merge into `BASE_BRANCH` |

`router/specs/arbiter.md` is a template the coordinator fills for a test dispute and runs as an ad hoc worker
(`router.py worker`). `router/contract-template.md` is the shape every contract follows. The smoke chain,
`router/templates/smoke.json` with `router/specs/smoke-write.md`, `smoke-ask.md` and `smoke-flaky.md`, proves the
router on harmless work: two workers at once, a question, a failure with a retry, a script step, a gate.

Every check in `router/checks/` prints one line, `OK …` or `NOT OK …`, and exits 0 or 1. A check that is not OK
pauses the chain and rings with every check line of the step.

## Profiles

The specs name no test runner, linter or path of one repository. A profile carries those as variables:
`router/profiles/<name>.json`, `{"name", "about", "vars": {…}}`. `inner-pr.json` defaults none of them, so a flow of
inner PRs names a profile.

| Profile | For |
|---|---|
| `python` | pytest under `tests/`, `pytest.ini`, ruff on the changed files, plain `git commit` |
| `typescript` | `npm test --`, `vitest.config.ts`, `tsc --noEmit` then eslint; run with `TESTS=` (the blind test writer is pytest-only) |
| `bell` | a repository whose test runner, lint and commit go through wrapper scripts the run directory provides |

Every variable, and what each shipped profile gives it:

| Variable | Meaning | `none` allowed | `python` | `typescript` | `bell` |
|---|---|---|---|---|---|
| `RULES` | A rules file every worker reads before anything else (standing rules, test limits). | yes: no rules file | `none` | `none` | `{SCRATCH}/briefs/rules-worker.md` |
| `TEST_CMD` | Starts a targeted test run; a spec appends paths or ids. Also used inside throwaway copies. | no | `python -m pytest -q` | `npm test --` | the limited pytest wrapper under a wall-clock bound, `PYTEST_PY={WT}/.venv/bin/python` |
| `UNIT_DIRS` | Where the fast tests live: the full unit suite (`{TEST_CMD} {UNIT_DIRS}`) and the validator's unit folders. | no | `tests` | `src` | `tests/unit` |
| `TEST_PATHS` | One git pathspec for every test file: what the implementer may only un-mark and what `/simplify` and the code fixer may not touch. A script step checks it. | no | `tests/` | `:(glob)**/*.test.ts` | `tests/` |
| `TEST_CONFIG` | One path: the test runner's settings, which no worker after the contract changes. A script step checks it. | no | `pytest.ini` | `vitest.config.ts` | `pytest.ini` |
| `COPY_SETUP` | Run inside a throwaway copy of the worktree (ledgers, test fixer, validator replay) before its first test run, so the tests there find the installed dependencies. | yes: the copy needs nothing | `none` | `ln -s {WT}/node_modules node_modules` | `none` |
| `LINT_CMD` | The lint gate, and a type-check when the command runs one (the `python` profile's does not), run from the worktree root with the shell variable `BASE` set to the PR's base commit, so it can pick the changed files. A pipeline starts with `set -o pipefail;`, so a failing `git diff` fails the gate. | yes: the validator reports "skipped: no lint command" | ruff on the changed `*.py` | `tsc --noEmit`, then eslint on the changed `*.ts`/`*.tsx` | `{SCRATCH}/bin/ci-lint-changed.sh "$BASE"` |
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

Every profile sets every one of: `RULES`, `TEST_CMD`, `UNIT_DIRS`, `TEST_PATHS`, `TEST_CONFIG`, `COPY_SETUP`,
`LINT_CMD`, `FROZEN_PATHS`, `EXTRA_SUITE`, `COMMIT_CMD`. Where a gate does not apply, the value is the literal
`none`, and the spec skips the gate and says so. A value may contain `{SCRATCH}`, `{WT}` and `{PR}`, and no other
placeholder.

**Write a profile:** copy the closest one, set every variable (the template's `"variables"` says what each means),
keep `TEST_PATHS` and `TEST_CONFIG` one word each, and run `python3 tests/test_router.py -k Profiles` in a checkout of
this plugin: it checks that each profile sets every variable and renders every spec. A repository's own profile may
live outside the plugin; the flow names it by path.

## The page

The mailbox daemon serves a page on `127.0.0.1` only, from a thread of its own: `router.py page` prints the URL,
`router.py page --open` opens it in an Orca browser tab (`orca tab create --url <url>`), and `router.py status`
shows it on its `page` line. `ROUTER_PORT` picks the port (default 0: a free one).

- **View:** the header (title, flow version, slots, the daemon and its last delivery, the state directory), four
  counters, the PRs as a graph (a lane per part, a column per depth of `after`, step chips with ✓ ▶ ◆ · ✗ –), the
  workers under their steps, the rings nobody picked up, the questions nobody answered, the flow's last versions and
  the journal's last lines. It polls `GET /state` every 5 s and redraws in place, so a click or a drag that starts
  before a poll still lands.
- **Edit the flow:** the same canvas on a local copy. Drag a pending step, drop a role from the palette into a PR,
  remove a step, change a step's agent, model or effort, add or drop a PR that has not started, change slots and
  start mode. Locked steps (🔒) refuse the drag. **Apply** shows the change lines of a dry run, asks for a note,
  then applies: the same function as `flow apply`, the same checks, the same history row (`by page`). A stale base
  answers 409 ("someone else changed the flow"); a refusal answers 422 with the router's lines next to the canvas.

The page asks Orca nothing and nothing in it leaves the machine. It accepts only a `Host` of
`127.0.0.1:<port>` or `localhost:<port>` and a `POST` from that origin. `progress.html` is still written for a
run nobody opens a browser on.

| Route | Answer |
|---|---|
| `GET /` and `GET /page/<file>` | the page (`router/page/index.html`, `page.js`, `page.css`) |
| `GET /state` | `progress_data` as JSON, plus `flow`: `version`, `slots`, `start`, `prs` (each with the steps the flow gives it, `started`, and `fixed`: how many of its first steps are settled), `palette`, `templates`, `removed`, the last 5 `history` rows |
| `GET /flow` | the router's copy, `flow.json` (404 before the first apply) |
| `POST /flow` | `{"base": <version>, "by", "note", "flow": {...}, "dry_run": false}`. `200 {"ok": true, "version", "changes", "started", "dry_run"}`; `409 {"ok": false, "reason", "current": <the copy>}`; `422 {"ok": false, "problems": [...]}`, nothing changed |

Anything else is a 404. The server answers only a `Host` of `127.0.0.1:<port>` or `localhost:<port>`, and a `POST`
only from that origin with a JSON body (403 and 415 otherwise; 400 for a `Content-Length` that is not a number), so
another site open in the same browser can neither read the run nor apply a flow. Each request has its own thread with
a 30 s socket timeout, and the body is read before `flow.lock` is taken, so a slow client holds nothing. A handler that
raises answers 500 and writes the traceback in `mailbox.log`; the mail loop never waits for the server.

## The collector

Once a worker is released, the mailbox daemon starts `router/collector.py` for that dispatch in a process of its
own. It waits for the chain to record the attempt's end and checks, then writes under the state directory only:

```
logs/<pr>/<step>/<dispatch>/        an ad hoc worker: logs/<pr or _none>/_adhoc/<dispatch>/
  meta.json          ids, attempt n, agent/model/effort and the "effective" launch when Orca started another,
                     started, ended, outcome, flow_version, cause, worktree, head range, check lines, release
  orca-read.json     Orca's archive of the worker, every page
  events/            the dispatch's messages and its liveness file
  session.jsonl      the matched Claude Code or Codex session file, byte for byte
  session.subagents/ the Claude session's subagent files, copied whole
  tokens.json        input, output, cache_creation, cache_read, by model, turns; the totals include the subagents,
                     and "subagents" gives their share
logs/index.jsonl     one row per dispatch, for the report
```

| Session match | When |
|---|---|
| `unique` | exactly one session file ran in the dispatch's worktree and overlaps its window; of several, the only one whose first lines name the dispatch id (Orca's preamble does), else the only one that began within 3 min of the start |
| `ambiguous` | several, and neither rule singles one out: the paths are recorded, nothing is copied |
| `none` | no such file, the worktree is unknown, or the agent is neither Claude nor Codex |

`index.jsonl` and `meta.json` hold ids, paths, timestamps and counts, never transcript text. Every collect prints
one summary line, `OK collected <n> · skipped <n> · locked <n> · not settled <n> · failed <n>`: `skipped` was
collected before, `locked` is another collector at work (exit 0: the daemon's and a `collect --all` may meet),
`not settled` has no end yet. A dispatch that fails to collect journals `collector: <dispatch>: <why>` and leaves no
temporary directory.

```sh
router.py collect --all                     # a run whose daemon was not collecting, or after a fix
router.py collect --dispatch <id> --force   # collect one again
router.py collect --dry-run                 # each dispatch's match and the counts; copies nothing
```

## The report

```sh
router.py report                                  # writes $ROUTER_STATE/report.html and prints its path
router.py report --metrics <file.json>            # the same numbers as JSON
router.py report --pr <pr> --out <file>           # one PR: its section, and the header and the metrics count it alone
router.py report --prices <file.json>             # per-model prices per million tokens; without it: "not priced"
```

One self-contained page per run (inline SVG, no script, light and dark). It reads the records and writes only the
files named on its command line: no Orca call, no session file opened. A number whose record is missing prints
`not recorded`, decided per record: without `journal.md`, gate waits, pauses, the rest, open waits, the ring kinds
(except silent rings, counted from `wake/` file names), checks not OK and questions say not recorded, while the
wall clock and the worker time still come from `state.json`. An empty `journal.md` is a real zero.

| Section | Says | From |
|---|---|---|
| a. Time per PR | the wall clock, split in four parts | `chains/<pr>/state.json`, `journal.md` |
| b. Steps | duration per step id (median, max), attempts by cause, checks not OK | `state.json`, `journal.md` |
| c. Interruptions | rings per kind, collector and locked lines, questions per role, time to answer | `journal.md`, `wake/`, event file names |
| d. Tokens | per requested model (`requested -> effective`), the subagents' share, by session model, cost | `logs/…/tokens.json`, `--prices` |
| e. Review yield | `FINDINGS: <n>` and `VERDICT: <WORD>` of review and triage steps | the attempts' summaries |
| f. Flow history | versions, who, why, the change lines; which PRs started under which version | `flow.json` |
| g. Lessons | the slowest step, the most-retried step, the role that asked the most questions, the longest wait at a gate (raw) | sections a to c |

**The time split is a partition of a PR's wall clock.** Each second goes to one part, in order: worker time (some
worker ran), then at gates (a gate waited), then paused, then the rest (starts, checks, scripts). Each part is zero
or more and the four add up to the wall clock. **The gate lesson is raw:** it is one gate's wait from `paused:
blocked` to the coordinator's line, how long the owner took to answer, while the split's "at gates" takes off the
seconds a worker ran, so the lesson can be larger. Each lesson names its PR, step, dispatch and the record it comes
from; `/flows:flow-plan` reads them back when planning the next run.

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
[profile](#profiles):

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

## The coordinator's loop

```
 init ──> flow apply ──> page --open ──> wait ──> a ring ──> rule on it ──┐
                                          ^                              │
                                          └──────────────────────────────┘
                     every chain done ──> collect --all (if needed) ──> report
```

| Step | Command |
|---|---|
| Bind | `orca orchestration run-create --objective "…" --json`, then `router.py init`. Another terminal taking over: `router.py init --run <run_id>` |
| Plan | `router.py flow apply <file> --dry-run`, then without `--dry-run` |
| Watch | `router.py status` (one screen), `router.py flow show`, `router.py page --open` |
| Wait | `router.py wait`, in the background, as the last action of every turn. It prints one screen ending with a `next:` line |
| Report | `router.py collect --all` when `status` shows fewer collected than settled, then `router.py report` |
| Stop | `router.py stop <pr>`, or `router.py stop --all` for every daemon; workers are never touched |

The answers to the rings:

| Ring | Answer |
|---|---|
| `WAKE question` | `router.py reply <message_id> "<answer>"`; the chain never paused |
| `WAKE gate` | do what the gate says, then `router.py resume <pr>` |
| `WAKE failed` | `router.py retry <pr> --note "<what to change>"` (`--agent`, `--model`, `--effort` change who) |
| `WAKE check` | fix it and `router.py resume <pr>`, or `retry`, or `resume <pr> --accept "<why>"` |
| send it back | `router.py resume <pr> --from <step> --note "<text>"` |
| `WAKE runner` | `router.py resume <pr> --set NAME=<value>`, or apply the flow when the flow sets it |
| `start unknown` | `router.py workers`; `resume <pr> --adopt <dispatch_id>` if Orca started it, else `retry` |
| a silent worker | `router.py workers`; only when Orca shows it exited, `router.py fail <pr> --why "…"`, then `retry` |
| `ACT:` (exit 3) | `router.py init`, then `resume <pr>` for each `RUNNER GONE` chain |

A one-off job outside a chain (an arbiter, a triage): `router.py worker "<label>" --spec-file <f> --agent claude
--model <id> --pr <pr>`; it rings when done, and its logs go under `_adhoc/`. `router.py last [n]` prints a ring
again after a compaction.

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

## Environment variables

Every `ROUTER_*` and `FLOWS_*` name the code under `router/` reads (`grep -o 'ROUTER_[A-Z_]*\|FLOWS_[A-Z_]*'
router/*.py | sort -u`, less `ROUTER_VARS`, which is a constant in `router.py`, not a setting), and the others it
reads:

| Variable | Default | Read by | Meaning |
|---|---|---|---|
| `ROUTER_STATE` | `$SCRATCH/router` | `router.py` | the state directory |
| `SCRATCH` | none | `router.py`, the specs | the run directory; fills `{SCRATCH}` when no layer sets it |
| `ROUTER_WAIT_MS` | 120000 | `router.py` | one mailbox long-poll |
| `ROUTER_SILENT_MIN` | 20 | `router.py` | minutes without a heartbeat before a live worker rings as silent |
| `ROUTER_START_TIMEOUT_MS` | 300000 | `router.py` | `worker-start --timeout-ms` |
| `ROUTER_MAX_CHAINS` | 2 | `router.py` | chains at once; a flow's `slots` replaces it |
| `ROUTER_POLL_S` | 3 | `router.py` | file poll interval |
| `ROUTER_REGISTRY_WAIT_S` | 10 | `router.py` | how long a message waits for its worker's start receipt |
| `ROUTER_PORT` | 0 (a free port) | `router.py` | the page's port on 127.0.0.1 |
| `ROUTER_COLLECT` | 1 | `router.py` | 0 turns off the automatic collection |
| `FLOWS_CLAUDE_PROJECTS` | `~/.claude/projects` | `collector.py` (passed on by the daemon) | where Claude Code writes session files |
| `FLOWS_CODEX_SESSIONS` | `~/.codex/sessions` | `collector.py` (passed on by the daemon) | where Codex writes session files |
| `FLOWS_ORCA_RETRY_S` | 2 | `collector.py` | the pause between two archive reads that answered `archive_not_ready` (in `collector.py --help`, not `router.py --help`) |
| `ORCA_CLI_COMMAND` | `orca` | `router.py`, `collector.py` | the Orca CLI |
| `ORCA_TERMINAL_HANDLE`, `ORCA_PANE_KEY` | set by Orca | `router.py init` | the coordinator terminal the daemons act for |

## Troubleshooting

Every refusal is one line on stderr, exit 1 (2 for bad usage), and changes nothing.

| It prints | Why | Do |
|---|---|---|
| `this terminal is bound to no Run: …` | `init` ran in a terminal with no Run | `orca orchestration run-create --objective "…" --json`, or `router.py init --run <id>` |
| `set ROUTER_STATE, or SCRATCH (the run directory)` | neither is exported | export one in every terminal that runs the router |
| `the mailbox daemon is not running: router.py init` | a chain was started with no daemon to take its messages | `router.py init` |
| `the mailbox daemon did not stay up: tail …/mailbox.log` | the daemon died at once | read the log; often another daemon holds the pid file |
| `NOT OK flow: <n> problem(s), nothing changed` | the flow file or the change was refused; one line per problem follows | fix each line ([What apply refuses](#what-apply-refuses)), dry-run, apply |
| `… base main: inner PRs go into the integration branch, never main or master` | a PR's `base` | name the integration branch |
| `… after names <x>, which is not in the flow` / `after: a cycle: …` | the graph | fix `after` |
| `… {X} is not a variable` | a typo, or a variable no layer gives nor the template declares | fix the name, or give it in `vars` |
| `stale: the run is at v<N>` | someone applied first (the page, another terminal) | read `router.py flow history`, redo the change on v<N> |
| `<pr>: step <s> is done: it cannot be removed` (or `…its definition cannot change`) | the change touches a settled step | change only pending steps; send work back with `resume --from` |
| `<pr>: its chain exists, so its after cannot change` | a started PR's `after` | leave it |
| `the flow owns <pr>: router.py flow start <pr>` | `chain` on a PR the flow names | `router.py flow start <pr>` |
| `the flow is the plan: …` | `plan` with a flow | change the flow instead |
| `no flow: router.py flow apply <file>` | `flow show`, `start` or `history` before the first apply | apply a flow |
| `<n> chains are open (…)` | no free slot for `chain` | wait, or raise `slots` with an apply |
| `<pr>: the flow sets <K>, …` | `resume --set` of a variable the flow sets | change it with `flow apply` |
| `<pr>: step <s> still has a live worker (…)` | `resume --from` while a worker runs | let it finish, or `fail` it once Orca shows it exited |
| `NOT OK <pr>: Orca shows <dispatch> as …` | `fail` while Orca still shows the worker live | wait; silence is never enough |
| `NOT OK <label>: the worker did not start; receipt: <file>` | `worker-start` refused an ad hoc worker | read the receipt |
| `the page is not served: …` | `page` with no daemon, or a daemon without its page | `router.py init`, or read `mailbox.log` |
| `NOT OK orca tab create answered …` | `page --open` could not reach Orca | open the URL `router.py page` printed |
| `collector: <dispatch>: <why>` (journal) | a collection failed; no chain is affected | fix the cause, `router.py collect --dispatch <id> --force` |
| a session match `ambiguous` | several session files in one worktree and window, none naming the dispatch | read the paths in `meta.json`; nothing is guessed |
| `refused: …` (in `mailbox.log`) | a `POST /flow` from the page was refused | the page shows the same lines next to the canvas |

A check prints `NOT OK …` and pauses its chain: the ring shows every check line of the step; fix the cause and
`resume`, or `retry` with the line as the note.

## Current status and limits

- The smoke flow ran end to end on real Orca (Orca 1.4.223, 2026-10-09): one PR, five Sonnet workers in a throwaway
  worktree, a question, a failure and its retry, a script step, a gate, two drags on the page in an Orca tab without
  a reload, each applied as a version, then `collect --all` (5 of 5 sessions unique) and the report.
  `inner-pr.json` has not run a real PR yet, and no profile has driven a real chain.
- The router does not create worktrees, intent files, or the scripts a profile names: the run directory must hold
  them before a PR starts.
- The page has no login: whoever reaches 127.0.0.1 on the machine can read the run and apply a flow, as from a
  terminal.
- The collector has not read a long run's archive; its `archive_not_ready` code is the stand-in's. Codex reports no
  cache writes; prices are the owner's file.
- A reboot is untested; the recovery is by construction (state on disk) and by the killed-process tests.

## Tested how (history)

2026-10-09, the product surface and the smoke pilot on real Orca (M5).

- The pilot, Orca 1.4.223, from a worker terminal with a Run of its own: one PR on `router/templates/smoke.json`
  with the `python` profile, its workers in a throwaway worktree (`path:{WT}`), slots 1, start auto. Five Sonnet
  workers: alpha and beta at once, a question answered with `reply`, a script step, flaky failed and retried with a
  note, the gate passed with `resume`; the chain took 2 min 37 s. In the Orca tab (`orca snapshot`, `click`, `drag`,
  `fill`), with no reload: a pending chip dragged and applied as v2, then a second drag applied as v3; the header
  showed each version and `flow history` lists both `by page`. Every click reached the page.
- The pilot found a collector miss: five workers one after another in one worktree began within 3 min of each
  other, so `collect --all` after the run matched 1 of 5 sessions (4 ambiguous). Each session's first message is
  Orca's preamble naming its dispatch; that id now singles the file out (`Collector`, 4 mutants, each killed). After
  the fix: 5 of 5 unique, `OK collected 5 · skipped 0 · locked 0 · not settled 0 · failed 0`, and the report read
  5 collected sessions: 157 s wall clock, 143 workers, 7 at the gate, 6 paused, 1 the rest.
- The report's gate lesson is labelled `longest wait at a gate (raw)` on the page and in the metrics (#43 R6);
  `metrics.golden.json` changes in that label only (`Report`, 1 mutant, killed).
- `python3 tests/test_router.py` in a `git archive HEAD` export: 204 tests, green (188 before). The export's first run failed one: FLOWS.md
  linked the missions guide by a path outside the plugin, which neither an export nor an installed copy has; the
  link is text now. The new
  class `Skills` (14) checks the skills and the docs against the code: frontmatter and length, every `router.py`
  command and flag named is in `--help`, every path named exists, the env table equals what `router/*.py` reads,
  the troubleshooting lines are the code's, flow-plan never writes a file the dry run rejects (the eval's reference
  flow passes, the same with `base: main` is refused and writes nothing), flow-report says when it collected first,
  flow-status reports the page's URL and collected of settled (as `status` prints them), no skill merges, marks
  ready or switches the account, the getting-started page requests nothing. 12 mutants of the skills and docs,
  each killed by its named test.
- `python3 tests/test_page.py`: 11 skipped under the system python3; 11 tests, green, with Playwright 1.63 in a scratch venv.
- `claude plugin eval` (2.1.295) cannot run the flow-plan case here: it refuses a Bash-granting case while
  `~/.aws/config` has a `credential_process`. The case stays in `evals/flow-plan/`.

2026-10-09, the run report (M4) with the page (M2) merged in, #42's round-2 lows, and review round 1 of the report.

- `python3 tests/test_router.py` in a `git archive HEAD` export of `plugins/flows`: 188 tests, green. The merge of
  `orca-router` gave 181 (#42's 165 and M4's 16; no class, fixture or `fake-orca` scenario shared), and this round
  adds 7. `Collector`: an expired lock that another collector takes over before this one's `mkdir` is counted
  `locked 1 · failed 0` and journaled once. `Report`: B1's review run through its check pause pins every part of the
  split (the rest 40 s, no minus sign); 300 generated runs (stdlib `random`, a fixed seed, workers, gates and pauses
  that overlap, some outside the wall clock) whose four parts are each a second-by-second count and add up to the wall
  clock; a run without `journal.md` (not recorded, the wall clock and worker time still numbers) and one with an empty
  journal (a real 0); `--pr A2` in the header and the metrics; an attempt with no end; and `report.py`'s imports and
  calls read from its syntax tree. `metrics.golden.json` is unchanged.
- `python3 tests/test_page.py` with Playwright 1.63: 11 tests, green.
- Mutants, each in a scratch copy: the rest from the raw gate and pause spans, gate waits not less the workers' time,
  gate waits that include the pauses, the journal's absence ignored, `--metrics` back to the whole run, an attempt
  without an end counted as 0, and the takeover's `FileExistsError` left to fail. Each one fails a named test.

2026-10-09, the run report (M4).

- `python3 tests/test_router.py` in a `git archive HEAD` export of `plugins/flows`: 164 tests, green (148 before). The
  16 new ones are the class `Report`, over `tests/fixtures/report-run/`, a synthetic state directory (3 PRs under two
  flow versions, 12 dispatches of which 2 ad hoc, one inside a PR and one outside any): the metrics equal
  `metrics.golden.json` (a difference prints its path); the golden's arithmetic, worked out in the test from the
  fixture's timestamps and `tokens.json` files; every golden number is on the page, the Codex session without counts
  says not recorded and the cost says not priced; a price file gives the cost table, one cost checked by hand and a
  Codex input priced without its cached part; neither the page nor the metrics hold the sentinel or any text of the
  fixture's session lines; a run without `logs/` still has its time, steps and interruptions; `router.py report`
  writes `report.html` in the state and prints its path, and `--pr` gives one section; ad hoc workers in their PR's
  section and outside any PR; totals that include the subagents and their share; `requested -> effective` in the
  tokens and the lessons tables; collector and locked lines apart from failed steps; the lessons and their records;
  two workers at once covered once; a pause with no coordinator line is open, never a gate's time; an attempt
  without a cause; and the report writes nothing in the state and opens no session file (they are made unreadable).
- 20 mutants of `report.py`, each in a scratch copy against `Report`: 20 caught. The one that survived the first pass
  (an attempt n 2 with no cause counted as first) has its own test now.
- Read-only over the group-B run's state directory: 14 PRs, 266 dispatches (104 ad hoc), 0 collected sessions (it has
  no `logs/`), 22 sections. The first pass found a negative rest in 8 of 14 PRs: a review group's workers run at once.
  The rest is now measured against the time workers covered.

2026-10-09, the page (M2) with the collector (M3) merged in, and review round 1 of the page.

- `python3 tests/test_router.py` in a `git archive HEAD` export of `plugins/flows`: 165 tests, green. The merge of
  `orca-router` gave 163 (M2's 142 and the 21 that M3 and its reviews added to the 127, no class or fixture shared),
  and this round adds 2: `Page` (a `Content-Length` that is not a number answers 400, and nothing is logged as
  raised) and `Collector` (a lock a third collector takes between the stat and the retry is counted `locked 1 ·
  failed 0`, journaled once, and left to it). The daemon serves the page and collects released workers at once.
- `python3 tests/test_page.py` with Playwright 1.63: 11 tests, green. The 3 new ones: the in-place draw, whose evidence
  is `test_a_redraw_keeps_the_elements_it_does_not_change` (a PR, its chips and the edit switch are the same elements
  across two polls that redraw, and a ref taken before them still clicks through); a guard for a flow reloaded during
  a drag, `test_a_drag_started_before_a_poll_still_completes` (the drop lands on the reloaded flow and applies; it
  passes on a full-replace draw too, so it is no evidence of the in-place one); a step dropped on the right half of
  the running step, and a role dropped there once no pending step is left, are taken, and the router applies them.
- Mutants, each in a scratch copy: the page emptied and rebuilt at each draw, a drop on a locked chip refused
  whatever its side, `Content-Length` read without its guard, and the retry's `FileExistsError` left to fail. Each
  one fails a named test.

2026-10-09, the page (M2).

- `cd plugins/flows && python3 tests/test_router.py`: 142 tests, green (127 before). The 15 new ones: the class `Page`
  (11: the routes and the 404s; `GET /state` with the rows and the flow; `GET /flow` is the copy; `POST /flow` with
  the current base makes one version and one history row, and its dry run writes nothing; a stale base answers 409
  and leaves `flow.json` byte for byte; a done step moved answers 422 with the router's line, as do a bad body and a
  `main` base, and a text body is 415; the page is on 127.0.0.1 at `ROUTER_PORT`, refuses another `Host` and another
  `Origin`; the mail loop settles two workers while two clients hold half-sent requests open; a handler that raises
  answers 500 and is logged while the daemon goes on; `router.py page` and `page --open` against the stand-in's `tab
  create`; two threads of one daemon take turns at `flow.lock`), `FlowVersions` (a permuted `after` and a re-spelled
  profile path are no change; `flow apply --dry-run`), `FlowValidation` (a shared value that fails for some PRs names
  them), `Profiles` (no profile sets a variable the specs cannot do without to `none`).
- `python3 tests/test_page.py` with Playwright 1.63 and its Chromium: 8 tests, green. A 2-PR flow on the daemon: the
  canvas shows both PRs, their steps and the arrow; a pending step dragged before another and applied is v2 with
  only that order changed, and the page's lines are the history's; a role dropped from the palette becomes a step
  with the role's usual model; a drag onto a done step is drawn refused and offers no Apply; a refused apply shows the
  router's line and keeps the edits; another terminal's apply first makes the page reload v2. View mode at 1400 px
  light and 900 px dark matches `tests/fixtures/page/view-1400-light.png` and `view-900-dark.png` (same size, at most
  1% of pixels off by more than 48 of 255). A node 20 px wider fails it. Without Playwright, the 8 are skipped with the
  install line.
- The Orca pilot, Orca 1.4.223: on a run of the stand-in, `router.py page --open` with the real `orca` opened the page
  in an Orca browser tab, and `orca snapshot` showed its header (the title, the run line, the View / Edit the flow
  switch). The router was not bound to the real Run: its mailbox daemon would have consumed the coordinator's
  messages. One `orca click` entered edit mode; later clicks answered `Clicked` and never reached the page, so
  `orca drag` was not tried. That run also showed that a redraw at every poll made Orca's element refs stale within
  5 s: the page now redraws only when the run changed, and (review round 1) in place, which may also be what lost
  the clicks after the first. Not tried again in Orca yet.

2026-10-09, the collector, review round 2 (M3).

- `python3 tests/test_router.py` in a `git archive HEAD` export of `plugins/flows`, not the working copy: 148
  tests, green (145 before). Round 1's head had committed `tests/fake-orca` without its executable bit, so its
  suite failed there while passing in the working copy. The 3 new ones, in `Collector`: `fake-orca` is executable;
  only the matched session's own `subagents/` is copied (a decoy under another session, and a session without
  one); a lock its owner removes between the failed `mkdir` and the `stat` is taken, and the dispatch collected.
  The held-lock test now checks that the lock's mtime is unchanged.
- Mutants, each in a scratch copy against `Collector`: the lock's mtime not restored after the `reported` marker,
  and any session's `subagents/` taken. Each one fails a named test.

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

## Not tested yet

- A flow of more than one PR has not run on real Orca; the smoke flow of one PR has (M5, in Tested how). Creating
  the worktrees a flow names is not part of the flow file.
- The collector has read five real Orca archives (the M5 pilot: two pages each, `contentComplete` false), never a
  long one, an `archive_not_ready` or a `source_changed`: those answers come from the fake, and the restart is
  taken from `worker-read --help`. Codex reports no cache writes, and
  older Codex clients no token counts: those fields say "not recorded". No token prices ship with the plugin: the
  report prices tokens from a file the owner gives (`--prices`).
- A collector that takes over an expired lock removes it first, unguarded: a second collector that removes it just
  after the first one made it again collects the same dispatch, which at worst writes its index row twice.
- The live page has shown one real Orca run in an Orca tab, with two drags applied from it (M5). Its browser tests
  run in headless Chromium.
- The page has no login: whoever can reach 127.0.0.1 on this machine can read the run and apply a flow, as from a
  terminal. No remote access, no layout saved per person; the run report is not on it.
- The view is local: a file and a command. Nothing is posted on the epic and nobody is notified.
- `stop <pr>` does not end a script step: the runner finishes the script first (a CI wait can take 90 minutes),
  and `stop` says `STILL RUNNING` after 15 s. The page shows the script as running until then.

- `draft-pr.sh` has never opened a real PR, and `router/templates/inner-pr.json` has never run. Its role specs were
  written on 2026-10-05 and no worker has run one. No profile has driven a real chain.
- Codex as a worker under the router, a `new-child` worktree, and a run longer than a few minutes are untested. A
  worker in another worktree (`path:{WT}`) ran in the M5 pilot.
- The router does not cap Claude workers. Two chains at once (`ROUTER_MAX_CHAINS`) give at most two Claude
  workers plus ad hoc ones. A chain holds its slot until it is done, also while it waits at a gate. The router does not cap test runs
  either: a profile's `TEST_CMD` and `COMMIT_CMD` can (the `bell` profile's wrapper lets one test run at a time across all workers).
- Bounded loops (a second review round, a survivor going back to a test writer) are not steps. They are the
  coordinator's decisions: an ad hoc worker, then `resume`.
- The report has read one real run's `logs/`, the M5 pilot's five dispatches. Cross-run comparison is not part of
  it; `/flows:flow-plan` reads one earlier run's lessons.

## Related

- [`README.md`](../README.md): what the plugin is, install, the test commands.
- [`FLOWS_GETTING_STARTED.html`](FLOWS_GETTING_STARTED.html): the first run, on the smoke template.
- `router.py --help` and `collector.py --help`: every command and file, from the code.
- [`skills/`](../skills): the four skills.
