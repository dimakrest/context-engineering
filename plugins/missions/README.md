# missions

Contract-first, multi-feature agent runs whose definition of done is written before any code.

- `/missions:mission-plan` — interview, validation contract (assertions with proof classes and proof
  budgets), features sized to files, milestones, caps.
- `/missions:mission-design` — architecture guidelines (D00n) with exemplars, before any code.
- `/missions:mission-run` — the orchestrator loop, driven by a Claude Code session: one worker at a
  time, blind per-feature review of a materialised patch, behaviour validation, convergence gate,
  advisory vs blocking halts, a reviewed draft PR as the terminal state.
- `bin/missions` — the same loop, driven by a program instead of a session: workers as subprocesses,
  a deterministic grade after each one exits, typed exit codes, unattended. **See *Running a mission*
  below for which to use.**
- `/missions:mission-status` · `/missions:mission-resume` · `/missions:mission-amend` ·
  `/missions:mission-crosscheck` · `/missions:mission-pr-review`.

Five agents (`mission-worker`, `mission-reviewer`, `mission-researcher`,
`mission-validator-scrutiny`, `mission-validator-behavior`), nine hooks, eight scripts, and the
out-of-process driver.
Everything is a file under `.missions/<slug>/` in the project you run it in; the plugin is
project-agnostic and learns the repo's rules from the mission's `state.md`.

Full guide: `docs/MISSIONS.md`. File schema: `templates/MISSIONS_TEMPLATES.md`.
First run: `docs/MISSIONS_GETTING_STARTED.html`.

## Install

```
/plugin marketplace add dimakrest/context-engineering
/plugin install missions@dimakrest-context-engineering
```

The hooks are inert in any project without an active `.missions/*/state.md`.

## Running a mission — two ways

Planning is the same either way: `/missions:mission-plan`, then `/missions:mission-design`, writing
the five files under `.missions/<slug>/`. What differs is what drives the loop afterwards.

| | **A session** — `/missions:mission-run` | **The driver** — `bin/missions run` |
|---|---|---|
| Decides the next action | a Claude Code session following the skill | a Python program (stdlib only, ≥ 3.9) |
| A worker is | a `mission-worker` subagent in that session | a separate process — `claude -p` or `codex exec` |
| Grades a handoff by | the orchestrator model reading it | the schema function plus `git`, after the process exits |
| Costs, per iteration | orchestrator tokens every turn | nothing outside the dispatches |
| After a compaction | `/missions:mission-resume` | nothing to resume — state is re-read from disk |
| Caps | hooks enforce them at each dispatch | checked in code before every paid dispatch |
| Ends with | prose, plus `resume_next` | a typed exit code (0–130), plus `resume_next` |
| Harness | Claude | Claude **or** codex |
| The `pr` phase | **yes** — draft PR, whole-branch review, `done` | **not yet** (#10) — it stops and hands the branch back |

Use a session when you want to watch it, intervene, or take the mission all the way to a reviewed
draft PR. Use the driver for long unattended stretches, a dollar cap enforced before every dispatch,
or a grade that does not depend on a model's judgment. Both read and write the same
`.missions/<slug>/`, so a mission can move between them — and today it must, because the driver
stops before the terminal steps.

*(If you have been calling these v1 and v2: yes, these are them. `v2` is avoided as a label here
because it already means the fenced `mission-state` block format — "the driver needs the v2 block".)*

### In a session

```
/missions:mission-run      # the loop: one worker at a time, blind validators at each milestone
/missions:mission-status   # an HTML page: coverage, spend, what's running, what's blocked
/missions:mission-resume   # after a compaction, a /clear, or a new day
```

The terminal steps live here: `phase: pr` → `/missions:mission-pr-review` → `phase: done`, and the
hooks go inert for that mission. A human merges.

### With the driver

```bash
git checkout -b mission/<slug>          # the branch state.md names; the driver never creates one
echo .missions/ >> .git/info/exclude    # preflight warns when .missions/ is tracked

M=plugins/missions/bin/missions         # or put bin/ on your PATH

$M init      .missions/<slug> --harness claude|codex   # writes driver.json.        Costs nothing.
$M preflight .missions/<slug>                          # refuses a bad setup.       Costs nothing.
$M run       .missions/<slug> --dry-run                # the queue and the real argv. Costs nothing.
$M run       .missions/<slug> --limit 1                # the first paid dispatch: one worker
$M run       .missions/<slug>                          # let it go
```

Full form:

```
plugins/missions/bin/missions init      .missions/<slug> --harness claude|codex [--stub-dir DIR] [--force]
plugins/missions/bin/missions preflight .missions/<slug>
plugins/missions/bin/missions run       .missions/<slug> [--limit N] [--milestone M] [--until validate|milestone] [--dry-run]
plugins/missions/bin/missions grade     .missions/<slug> F0nn [--self] [--json]
```

Run it **from the checkout, on the mission branch** — preflight refuses a detached HEAD or the wrong
branch, because the worker's git hooks reject commits off the one `state.md` names. `--until
validate` stops when the milestone's features are done and VALIDATE would begin; `--until milestone`
stops after it closes. `grade` is the check the worker is told to run on itself before it exits
(`--self`); the driver applies the same one after.

Then read the exit code:

| Exit | Reason | What it wants from you |
|---|---|---|
| `0` | done | nothing — the last milestone closed |
| `1` | error | read the run directory it names |
| `2` | preflight-failed | fix the setup; nothing was dispatched |
| `3` | limit-reached | nothing — `--limit` / `--until` did what you asked |
| `4` | budget | a cap raise in `mission.md`, journaled with the reason — or stop here |
| `5` | gate-blocked | a decision: unblock a feature, amend the plan, reconcile the branch |
| `6` | authority | an action the driver has no authority to take |
| `7` | contract | the contract is wrong — `/missions:mission-amend` |
| `8` | provider-quota | wait for the reset, then run again |
| `130` | interrupted | check the active feature against git, then run again |

Re-running is always safe: a closed milestone is never re-entered, and a validation round resumes at
the step it did not reach. Every stop rewrites `resume_next` in `state.md` with where it stood.

The driver writes `driver.json`, `runs/<task>/`, `githooks/` and `.driver.lock` into the mission
directory **and nothing else new** — `validation/`, `patches/`, `followups.md` and the rest are the
same files in the same shapes the skills write, and the hooks keep working alongside it (it takes
and releases `.writer` / `.lease` in their format). A `codex` worker runs your `~/.codex/hooks.json`
hooks and gets no dollar budget — its cost is reported in tokens.

## What the hooks enforce

| Guard | How |
|---|---|
| One writer at a time | `.missions/<slug>/.writer`, taken and released by the hooks |
| One executor (test runner) at a time | `.missions/<slug>/.lease` — anything with Bash |
| Reviewers stay blind | reviewer prompts must name a patch file and may not run git; the reviewer's own shell is refused `git log/show/diff`, `gh`, `graphify prs` and handoff paths |
| Index spend stays visible | `repowise update` / `init` without `--index-only`, `graphify label/extract`, `cluster-only` without `--no-label` are blocked for every caller — they bill an LLM outside the caps |
| Spend is measured | dollar cap from the harness's cost-state; dispatch, wall-clock and repair-round caps from the journal |
| State stays small | `state.md` capped at 200 lines; agents are briefed with a ≤ 2 KB digest |
| No push outside phase `pr`, no merge, no `--no-verify` | commit-discipline |

## How the driver works

Reference for `bin/missions` — see *Running a mission* above for the commands. What is not
driven yet: the terminal steps and the push (the `pr` phase, #10), `status` (#19), `resume`
and sleep-and-resume on a provider quota (#7).

**codex needs unprivileged user namespaces.** On Linux `codex exec` sandboxes every command it
runs with bubblewrap, which needs a user namespace it can write a uid map in. Plenty of hosts
refuse that — an unprivileged container, a hardened kernel — and the failure is quiet and
expensive: bwrap exits before the shell for *every* command, so the worker reads no file, runs no
test, explains the blockage in prose and exits 0. That is a truthful `no_op`, but the driver would
then tell you the brief is not landing. `init` and `preflight` both ask the question first, so it
costs nothing — `init` because the config it has just written is the one that would be refused, and
the fix is a line in that file:

```
$ missions init .missions/demo --harness codex
wrote …/driver.json (harness codex, branch mission/demo)
warning: codex sandbox 'workspace-write' needs an unprivileged user namespace, and this host refuses one …
`missions preflight .missions/demo` will refuse this config until that is settled.
```

Run somewhere user namespaces are allowed, or — **only when the host is already a sandbox you
accept the worker having the run of**, such as a container or VM — set `adapters.codex.sandbox`
to `"danger-full-access"` in `driver.json`. That turns codex's own sandbox off and leaves the
driver's env whitelist, git hooks, blindness and post-exit grade as the enforcement. It is a
deliberate choice and never a default: `init` warns, `preflight` refuses, and neither writes it
for you.

**Grading happens once, after exit (#4).** A launch grades nothing. When the worker process is
gone the driver grades the handoff — the schema function `hooks/mission-handoff-schema.sh`, the
commit on the mission branch's own ref (a detached checkout cannot make a commit count by sitting
on it), a clean tree, the checkout still on the mission branch, no merge commit and the `F0nn:`
prefix on every commit since launch, claims within the feature's
assertions and, for a `complete` handoff, every one of them claimed — keyed to the attempt that
ran (`F012#2`): a handoff left by an earlier attempt is not this one's. A run after which the
launch commit is no longer on the branch (a rebase, a reset) halts the mission: the earlier
features' ranges point at history that is gone. The contract is marked
`claimed` only for what the handoff claims. A handoff that says `blocked` halts the mission with
its own reason on the decision card; `partial` is re-dispatched with its "Left undone" as the
rejection. The worker is told to run the same check before it exits (`missions grade … --self`);
the two agree by construction, and `--self` says what the driver will do with a partial or
blocked handoff. Every run ends in one of nine
classes — `done` · `handoff_missing` · `malformed_handoff` · `tests_failed` · `budget_exhausted` ·
`infra_quota` · `infra_crash` · `stalled` · `no_op` — and `runs/<task>/outcome.json` records the
class with the grade. The old `PostToolUse: Agent` wiring of the schema hook is gone: it graded at dispatch, which
is the wrong moment, and fired 29 false alarms across the recorded runs.

**The watchdog.** While a worker runs, a thread polls the branch, the handoff, the run's output and
the tree (`--no-optional-locks`, so it never takes the index lock from under the worker). A commit
with no handoff is journaled as `commit_observed` the moment it is seen; if nothing then happens
for `watchdog.commit_no_handoff_s` (default 300) the run is ended, the driver **reconstructs** the
handoff from the commit — first line `reconstructed by the driver`, no test evidence claimed —
grades it once, ingests it and moves on. Only a run that ended on its own terms (that watchdog
verdict, or a clean exit) is reconstructed `complete`; one cut off by its deadline or a crash
after a WIP commit is reconstructed `partial`, claims nothing, and the next attempt is told to
continue from the commit. `watchdog.silence_s` ends a run that changes nothing at all for that
long; it is off by default because `claude -p --output-format json` prints only at the end, so
silence there is not evidence. Both live in `driver.json`; `null` turns a rule off. A quota or
rate-limit message in the harness's own error text or stderr (never the worker's transcript) is
`infra_quota`, even after a WIP commit: the feature goes back to pending and the driver exits `8`
without halting the mission — wait for the reset, run again (the driver's own sleep-and-resume
is #7). A 529 `overloaded` is a crash and is retried like one.

**The driver's own cap, and its grace (#21).** A role's `budget_usd` is what its *work* is meant to
cost; the harness is told to cut at that plus `budget_grace_pct` (default 10). The gap is the
wrap-up window — a run that reaches its budget must still be able to write its handoff and commit,
and a cut exactly at the budget lands on whatever it was doing, losing the whole run for its last
cent. `mission.md`'s dollar cap is untouched and still binds before every dispatch. When the cap
does bind, the adapter that set it reports so from the harness's own words (`error_max_budget_usd`
under claude; codex has no budget flag), and a cap is never read as a defect: a run cut off after it
wrote a complete handoff and landed the commit is `done`, because every check the driver makes of
any other run passed; one cut off with the work unfinished is `budget_exhausted`, which stops the
mission at exit `4` naming the cap and the spend. It is never re-dispatched — the same cap ends the
next attempt in the same place, and before this the loop spent four times the cap discovering that.
The worker's own `blocked` is the one verdict a cap does not touch.

**Prep: enforcement without harness hooks (#13).** A run's environment is built from a whitelist,
never inherited: `PATH`, `HOME`, locale and proxy variables, the harness's own auth (`ANTHROPIC_*`
under claude, `OPENAI_*` under codex), `MISSIONS_*`, and the names `driver.json`'s
`env.passthrough` lists — every other `*_TOKEN` / `*_SECRET`, `GH_TOKEN` and `SSH_AUTH_SOCK` is
gone. `GIT_ASKPASS`, `GIT_SSH_COMMAND` and `GH_CONFIG_DIR` are stripped and then *re-set* to the
driver's own (an askpass that refuses, an empty gh config), so their value is the defense and
their absence would be a different bug. `runs/<task>/env-names.txt` records which names the child
was actually given (never values), written at the spawn site after any adapter additions.
The child's global gitconfig is a driver-written file carrying the checkout's identity and an
empty credential helper, and `GH_CONFIG_DIR` points `gh` at an empty directory under
`githooks/`. The guarantee model is layered, and each layer claims only what it holds. The
credential layer means **no credential in the run's environment**: no token, no agent socket, no
askpass, an empty credential helper, an empty gh config — what `HOME` holds stays readable
(`~/.ssh` keys, `~/.netrc`), so it is not "no credential on the machine". The hook layer
**refuses a cooperating worker**: a commit off the mission branch, a message without the `F0nn:`
prefix, a merge, a rebase, a push over a transport that needs no credential (a local path) meet
hooks that exit 1, and roles other than the worker cannot commit at all — while `--no-verify`,
`git -c core.hooksPath=`, an unset `GIT_CONFIG_COUNT` or an edit of `.missions/<slug>/githooks/`
bypass them, and the operator's global `core.hooksPath` is not chained (the child's global config
is the driver's). The post-exit grade is the gate that does not depend on the worker. The hooks
live in `.missions/<slug>/githooks/` and reach git only through `GIT_CONFIG_*` variables in the
run's environment, so the repo's config is never written and nothing needs restoring after a
crash; the repo's own hooks (pre-commit framework, husky) run first and keep their exit code. A
path staged outside the feature's `Files` draws a warning at commit and a rejection from the
grade after exit when the handoff does not name it. Under the claude harness the plugin's own
hooks stay installed and keep working; they are a bonus, never what the driver relies on. A
reviewer is blind by having nothing to look at: for its run's lifetime `handoffs/`,
`validation/`, `decisions/` and every other run's directory sit under `.blind/<task>/` (mode 000)
and come back in `finally` — a file written where a hidden one belongs is moved to
`.strays/<task>/`, the original wins. Only the driver that holds the mission's lock restores
what a crashed one left under `.blind/`; `missions preflight` and a dry run warn and leave it.

**VALIDATE and the judgment steps.** When every feature of the milestone is done the driver runs
the skill's sequence itself: scrutiny (one executor run) → a blind review per feature, serial →
behavior (only when the milestone has `interface` / `conversational` assertions) → negotiate →
converge → archive → the next milestone. Each validator's final message lands in
`validation/M1-<step>.md` (`-r2` for a repair round) under a header naming the task that wrote
it, and its per-assertion table is parsed and journaled as `verdict`. `proven` is written only
from those verdicts, one round at a time — structural from the reviewers' `satisfied`, interface
and conversational from the behavior validator's `proven`; every verdict of the round must agree,
one negative blocks (and moves a row an earlier round proved back to `claimed`), and once a
repair feature was reviewed only its review counts for the assertion it repairs — never from a
handoff, never by the negotiate step. Negotiate, and triage of the open issues a handoff raised,
are *judgment* steps: the model proposes, the driver applies. A judgment run is read-only, takes
no lease, answers with one JSON object checked against a schema in code (an assertion id the
contract does not have is such an error), and is re-run once with the error appended before the
driver stops with `error`. What it proposes is registered in the 0.2 shapes: findings become
`followups.md` entries (`(from M1-review-F001)`, clustered, dispositioned); every cluster
dispositioned `repair` becomes one repair feature `### F0nn` carrying `- **Repairs:** C01 (FU001)
of F001` (the feature/file gate in `check.sh` skips those; a repair with no assertion carries no
Assertions line), and its assertions are routed to it in `contract.md`. One cluster, one repair
feature: a cluster whose repair feature is still pending takes later findings into it, and one
whose repair already ran is a new root cause and gets a fresh cluster id, so the registry never
shows a cluster split across features. Repairs send the loop back to implementing with an
advisory `halt` in the journal; the repair-round cap (`Repair rounds per assertion`) and a
milestone-round cap halt the mission with the serial guard's wording. `contract_wrong` stops with
`contract` (exit 7); an unproven assertion nobody proposes to repair, a convergence failure, and
the `halt at every milestone` autonomy ceiling stop `gate-blocked`.

**Flags and the host.** `--until validate` stops when the milestone's features are done and
VALIDATE would begin; `--until milestone` stops after it closes. Every executor run — worker,
reviewer, scrutiny, behavior — takes `~/.missions/host.lock` (fcntl; `MISSIONS_HOST_LOCK`
overrides the path), so two missions in two worktrees never run their tests at the same time; a
driver that waits journals `lease_wait` naming the holder. `"host_lease": false` in `driver.json`
opts out (preflight warns). `driver.json` also carries per-role `timeout_s` / `budget_usd` /
`model` under `roles`, the mission-wide `budget_grace_pct` (default 10; `0` or `null` turns it
off), and `env.passthrough`, the operator's explicit list of extra variable
names (or `PREFIX_*` globs) the runs may see.

## Developing

Iterate against the local checkout without pushing:

```
claude --plugin-dir /path/to/context-engineering/plugins/missions
```

Run the regression suite (every hook and script against fixture missions, inertness first):

```
bash plugins/missions/tests/run.sh
```

Cases live in `tests/gen-cases.py` (one `case(...)` call each: a script, a stdin payload, a fixture
mission tree, and an `expect` of `rc=`, `stderr~=`, `stdout~=`, `postcheck=`); `run.sh` regenerates
`tests/cases/` from it on every run. Add a case for every new block. To see what a hook actually
receives from the harness, run a session with `MISSION_HOOK_DEBUG=1` and read
`.missions/<slug>/.hook-debug.log`. Bump `.claude-plugin/plugin.json` on every behaviour change; the
marketplace fetches by version.

Trace tests run the real driver over a temporary repo with a stub worker (a shell script):

```
bash plugins/missions/tests/traces/run.sh            # all traces + tests/driver-selftest.py
bash plugins/missions/tests/traces/run.sh 'two-*'    # one case
```

A case is a directory under `tests/traces/` that overlays `_base/` (the fixture repo, mission and
stub) and an `expect` of `rc=`, `journal~=` (in order), `git~=`, `state~=`, `file=`, `postcheck=`;
`run.sh`'s header documents every key. A failed case keeps its tmp dir under `tests/traces/.out/`.

Mutation tests check that those traces bite:

```
bash plugins/missions/tests/mutants.sh              # every mutant
bash plugins/missions/tests/mutants.sh 'skip-*'     # one
```

Each case under `tests/mutants/` breaks one rule — continuation, identity, approval, freshness,
enforcement or evidence — on a throwaway copy of the plugin, and asserts that the trace defending that rule
now **fails** while a control trace still **passes**. The second half is what keeps a mutant
honest: a mutation that reddens everything proves nothing — so pick a control that *executes* the
mutated line without depending on it. The `breaks` trace is run once on the unmutated copy first,
so a renamed trace cannot read as a passing mutant, and an anchor that no longer matches the
driver is reported as `anchor not found` rather than a quiet pass.

The paid live smoke — **never run by any suite**, it spends real money:

```
bash plugins/missions/tests/harness/run.sh claude|codex|both
```

One real worker run over the fixture repo with a $2.00 budget, asserting the journal shape
(`dispatch` → `agent_return` → `cost` → `step_done`, cost in usd under claude and tokens under
codex), that the class means the worker did the work, that a commit landed, and that no credential
reached the child's own environment. `both` runs each adapter and compares the shape, the class and
the evidence — the harness-agnostic claim as a test.
