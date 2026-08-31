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
`mission-validator-scrutiny`, `mission-validator-behavior`), nine hooks, seven scripts.
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
| Reviewers stay blind | reviewer prompts must name a patch file and may not run git |
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
