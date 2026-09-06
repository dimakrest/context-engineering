# missions

Contract-first, multi-feature agent runs whose definition of done is written before any code.

- `/missions:mission-plan` — interview, validation contract (assertions with proof classes and proof
  budgets), features sized to files, milestones, caps.
- `/missions:mission-design` — architecture guidelines (D00n) with exemplars, before any code.
- `/missions:mission-run` — the orchestrator loop: one worker at a time, blind per-feature review of
  a materialised patch, behaviour validation, convergence gate, advisory vs blocking halts, a
  reviewed draft PR as the terminal state.
- `/missions:mission-status` · `/missions:mission-resume` · `/missions:mission-amend` ·
  `/missions:mission-crosscheck` · `/missions:mission-pr-review`.

Five agents (`mission-worker`, `mission-reviewer`, `mission-researcher`,
`mission-validator-scrutiny`, `mission-validator-behavior`), nine hooks, eight scripts.
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

### The driver (0.3, in progress — #5)

`bin/missions` is the out-of-process driver: a Python program (stdlib only, ≥ 3.9) that owns the run
loop instead of a Claude session deciding whether to take another turn. It drives a mission from
its first feature to its last milestone's close — select the pending feature, render its prompt,
run a worker as a blocking subprocess under `claude -p` or `codex exec`, grade the handoff after
the process exits, write `features.md` / `contract.md` / `state.md` / `journal.jsonl`, loop; when
a milestone's features are all done, run VALIDATE the same way (below) — and stops with a typed
reason and exit code (`0` done · `1` error · `2` preflight-failed · `3` limit-reached · `4` budget ·
`5` gate-blocked · `7` contract · `8` provider-quota · `130` interrupted). Not driven yet: the
terminal steps and the push (the `pr` phase, #10); `resume`, `status` and the mutation tests (#6).

```
plugins/missions/bin/missions init      .missions/<slug> --harness claude|codex
plugins/missions/bin/missions preflight .missions/<slug>
plugins/missions/bin/missions run       .missions/<slug> [--limit N] [--milestone M] [--until validate|milestone] [--dry-run]
plugins/missions/bin/missions grade     .missions/<slug> F0nn [--self] [--json]
```

Run it from the checkout on the mission branch. It writes `driver.json`, `runs/<task>/`,
`githooks/` and `.driver.lock` into the mission directory and nothing else new — `validation/`,
`patches/`, `followups.md` and the rest are the 0.2 files, written in their 0.2 shapes; the 0.2
hooks keep working alongside it (it takes and releases `.writer` / `.lease` in their format). Note that a `codex`
worker runs your `~/.codex/hooks.json` hooks and gets no dollar budget — cost is reported in
tokens.

**Grading happens once, after exit (#4).** A launch grades nothing. When the worker process is
gone the driver grades the handoff — the schema function `hooks/mission-handoff-schema.sh`, the
commit on the mission branch's own ref (a detached checkout cannot make a commit count by sitting
on it), a clean tree, the checkout still on the mission branch, claims within the feature's
assertions and, for a `complete` handoff, every one of them claimed — keyed to the attempt that
ran (`F012#2`): a handoff left by an earlier attempt is not this one's. The contract is marked
`claimed` only for what the handoff claims. A handoff that says `blocked` halts the mission with
its own reason on the decision card; `partial` is re-dispatched with its "Left undone" as the
rejection. The worker is told to run the same check before it exits (`missions grade … --self`);
the two agree by construction, and `--self` says what the driver will do with a partial or
blocked handoff. Every run ends in one of eight
classes — `done` · `handoff_missing` · `malformed_handoff` · `tests_failed` · `infra_quota` ·
`infra_crash` · `stalled` · `no_op` — and `runs/<task>/outcome.json` records the class with the
grade. The old `PostToolUse: Agent` wiring of the schema hook is gone: it graded at dispatch, which
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

**Prep: enforcement without harness hooks (#13).** A run's environment is built from a whitelist,
never inherited: `PATH`, `HOME`, locale and proxy variables, the harness's own auth (`ANTHROPIC_*`
under claude, `OPENAI_*` under codex), `MISSIONS_*`, and the names `driver.json`'s
`env.passthrough` lists — every other `*_TOKEN` / `*_SECRET`, `GH_TOKEN`, `SSH_AUTH_SOCK`,
`GIT_ASKPASS` is gone, and `runs/<task>/env-names.txt` records which names ran (never values).
The child's global gitconfig is a driver-written file carrying the checkout's identity and an
empty credential helper. The guarantee model is layered, and honest about which layer does what:
credentials make a push **impossible** — git has nothing to authenticate with; hooks make what is
still possible **refused** — a commit off the mission branch, a message without the `F0nn:`
prefix, a push over a transport that needs no credential (a local path) meet hooks that exit 1,
and roles other than the worker cannot commit at all. The hooks live in
`.missions/<slug>/githooks/` and reach git only through `GIT_CONFIG_*` variables in the run's
environment, so the repo's config is never written and nothing needs restoring after a crash; the
repo's own hooks (pre-commit framework, husky) run first and keep their exit code. A path staged
outside the feature's `Files` draws a warning at commit and a rejection from the grade after exit
when the handoff does not name it. Under the claude harness the plugin's own hooks stay installed
and keep working; they are a bonus, never what the driver relies on. A reviewer is blind by having
nothing to look at: for its run's lifetime `handoffs/`, `validation/`, `decisions/` and every other
run's directory sit under `.blind/<task>/` (mode 000) and come back in `finally`; `preflight`
restores whatever a crashed driver left there.

**VALIDATE and the judgment steps.** When every feature of the milestone is done the driver runs
the skill's sequence itself: scrutiny (one executor run) → a blind review per feature, serial →
behavior (only when the milestone has `interface` / `conversational` assertions) → negotiate →
converge → archive → the next milestone. Each validator's final message lands in
`validation/M1-<step>.md` (`-r2` for a repair round) under a header naming the task that wrote
it, and its per-assertion table is parsed and journaled as `verdict`. `proven` is written only
from those verdicts — structural from the reviewer's `satisfied`, interface and conversational
from the behavior validator's `proven`, the latest verdict per validator winning — never from a
handoff, never by the negotiate step. Negotiate, and triage of the open issues a handoff raised,
are *judgment* steps: the model proposes, the driver applies. A judgment run is read-only, takes
no lease, answers with one JSON object checked against a schema in code, and is re-run once with
the error appended before the driver stops with `error`. What it proposes is registered in the
0.2 shapes: findings become `followups.md` entries (`(from M1-review-F001)`, clustered,
dispositioned); every cluster dispositioned `repair` becomes one repair feature `### F0nn` carrying
`- **Repairs:** C01 (FU001) of F001` (the feature/file gate in `check.sh` skips those), and its
assertions are routed to it in `contract.md`. Repairs send the loop back to implementing with an
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
`model` under `roles`, and `env.passthrough`, the operator's explicit list of extra variable
names (or `PREFIX_*` globs) the runs may see. `bash tests/harness/run.sh claude|codex` is the paid
smoke: one real worker run over the fixture repo with a $0.50 budget, asserting the journal shape
(`dispatch` → `agent_return` → `cost` → `step_done`, cost in usd under claude and tokens under
codex) and that no `GH_TOKEN` reached the run; the suites never run it.

Trace tests run the real driver over a temporary repo with a stub worker (a shell script):

```
bash plugins/missions/tests/traces/run.sh            # all traces + tests/driver-selftest.py
bash plugins/missions/tests/traces/run.sh 'two-*'    # one case
```

A case is a directory under `tests/traces/` that overlays `_base/` (the fixture repo, mission and
stub) and an `expect` of `rc=`, `journal~=` (in order), `git~=`, `state~=`, `file=`, `postcheck=`;
`run.sh`'s header documents every key. A failed case keeps its tmp dir under `tests/traces/.out/`.
