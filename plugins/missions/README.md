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
loop instead of a Claude session deciding whether to take another turn. Today it drives the
IMPLEMENT phase of one milestone — select the pending feature, render its prompt, run a worker as
a blocking subprocess under `claude -p` or `codex exec`, grade the handoff after the process exits,
write `features.md` / `contract.md` / `state.md` / `journal.jsonl`, loop — and stops with a typed
reason and exit code (`0` done · `1` error · `2` preflight-failed · `3` limit-reached · `4` budget ·
`5` gate-blocked · `8` provider-quota · `130` interrupted). VALIDATE, the judgment steps, `resume`
and `status` follow in #13 and #6.

```
plugins/missions/bin/missions init      .missions/<slug> --harness claude|codex
plugins/missions/bin/missions preflight .missions/<slug>
plugins/missions/bin/missions run       .missions/<slug> [--limit N] [--milestone M] [--dry-run]
plugins/missions/bin/missions grade     .missions/<slug> F0nn [--self] [--json]
```

Run it from the checkout on the mission branch. It writes `driver.json`, `runs/<task>/` and
`.driver.lock` into the mission directory and nothing else new; the 0.2 hooks keep working
alongside it (it takes and releases `.writer` / `.lease` in their format). Note that a `codex`
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

Trace tests run the real driver over a temporary repo with a stub worker (a shell script):

```
bash plugins/missions/tests/traces/run.sh            # all traces + tests/driver-selftest.py
bash plugins/missions/tests/traces/run.sh 'two-*'    # one case
```

A case is a directory under `tests/traces/` that overlays `_base/` (the fixture repo, mission and
stub) and an `expect` of `rc=`, `journal~=` (in order), `git~=`, `state~=`, `file=`, `postcheck=`;
`run.sh`'s header documents every key. A failed case keeps its tmp dir under `tests/traces/.out/`.
