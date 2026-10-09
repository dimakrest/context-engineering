---
name: flow-plan
description: Write a flow file with the owner - read the issue or checklist they name, propose the PR graph (ids, parts, titles, after, base), the template per PR, the repository profile, slots and start mode, then each PR's variables, and loop on `router.py flow apply --dry-run` until it validates. Ends by writing the file where the owner says and printing the `flow apply` command; never starts a worker. Use when the owner says "/flows:flow-plan", "plan the flow", "write a flow file", or has an epic, issue or checklist to turn into a run of inner PRs.
---

# /flows:flow-plan — write the flow file with the owner

A flow file is the run's plan: the PR graph, each PR's chain template and its variables, in one JSON file that
`router.py flow apply` takes. This skill writes that file with the owner and checks it with a dry run. It does not
apply it, start a chain or start a worker: the owner applies it with [`/flows:flow-run`](../flow-run/SKILL.md).

```sh
R=${CLAUDE_PLUGIN_ROOT}/router/router.py
```

[FLOWS.md](../../docs/FLOWS.md) has the schema, the variable layers and every refusal. This page is the order to
ask in.

## 0. What the owner gives

- **The source of the PRs:** an issue (`gh issue view <n> --comments`), an epic with sub-issues, a checklist in a file,
  or a list in the conversation. Read it whole before proposing anything.
- **The run directory** (`SCRATCH`), where the router's state and the workers' files go. Never inside the repository.
- **Where the flow file goes.** Default `$SCRATCH/flow.json`.

Ask for what is missing in one message, not one question at a time.

## 1. Read the lessons of the last run, if there is one

When the owner names an earlier run's state directory, build its report into a fresh directory, without writing into
the old state:

```sh
OUT=$(mktemp -d)
ROUTER_STATE=<old state> $R report --out $OUT/last-run.html --metrics $OUT/last-run.json
```

and read `lessons` in `$OUT/last-run.json`: the slowest step, the most-retried step, the
role that asked the most questions and the longest wait at a gate, each with the record it comes from. The gate lesson
is the raw wait, from `paused: blocked` to the coordinator's line; the time split's "at gates" is that wait less the
time workers ran. Say which lesson changes this plan (a step that always needs a retry gets a higher effort; a role
that asks many questions gets a fuller `RUN_CONTEXT`). No earlier run: skip this step and say so.

## 2. Propose the PR graph

One PR per checklist item, unless two items cannot be reviewed apart. Show it as a table, then wait for the owner:

| Field | What to propose |
|---|---|
| `id` | short, stable, a letter then digits by part (`A1`, `A2`, `B1`): letters, digits, `.`, `_`, `-`; case does not count |
| `part` | the group it is drawn in: `Part 1 · Dependencies` |
| `title` | one line, what the PR does |
| `after` | the ids whose chain must be done first. Only real dependencies: a PR that only reads what another adds |
| `base` | the integration branch it merges into. **Never `main` or `master`**: `flow apply` refuses them |

Keep the graph shallow: every `after` edge is a serial wait. Two PRs that touch the same files go in sequence.

## 3. Template, profile, slots, start

- **Template per PR.** `inner-pr` (`${CLAUDE_PLUGIN_ROOT}/router/templates/inner-pr.json`, 17 steps: contract,
  tests, implementation, validation, draft PR, reviews, fixers, CI, two gates) for real work; `smoke`
  (`templates/smoke.json`) only to prove the router on harmless work. A PR may carry its own `steps` (a template's
  step list shape) when it needs fewer or other steps; say why in the conversation.
- **Profile.** Ask which repository shape. Default from the languages in the repository: `pyproject.toml`,
  `setup.py` or `pytest.ini` → `python`; `package.json` or `tsconfig.json` → `typescript`. `ls
  ${CLAUDE_PLUGIN_ROOT}/router/profiles` lists the shipped ones (`python`, `typescript`, `bell`). When none fits, say so
  and offer to copy the closest one into the run directory and set each variable (FLOWS.md, "Write a profile");
  `inner-pr` defaults none of the profile's variables, so a flow of inner PRs without one waits forever.
- **`slots`.** Chains open at once. Default 2. A chain holds its slot through its gates.
- **`start`.** `auto` (the router starts a PR once it is ready) or `manual` (only `router.py flow start <pr>`). Default
  `auto`; propose `manual` for a first run of a new template.

Write every path absolute: `templates` and `profile` take a path relative to the flow file or absolute, and the
plugin's directory is `${CLAUDE_PLUGIN_ROOT}`, written out.

## 4. Variables

Flow-wide `vars` first, then each PR's:

| Variable | Ask |
|---|---|
| `RUN_CONTEXT` | one or two sentences every worker should know (flow-wide) |
| `WT` | each PR's worktree: `{SCRATCH}/wt/{PR}` in the flow's `vars` names them all. The router does not create worktrees: say who makes them before the PR starts |
| `ISSUE` | the sub-issue number whose "For the agent" comment is the PR's intent; its text goes to `$SCRATCH/intent/<n>.md` |
| `TITLE` | the PR title, `A1: …` |
| `TESTS` | `1` (a blind test writer runs) or empty for a PR that only moves a dependency version |
| `LEDGER` | `1`, or empty for a PR with no code to mutate |
| `GREEN_PINNED` | `1` when its tests pin behaviour the old code already has |
| `IMPL_EFFORT` | `high`, `xhigh` for the hardest PR of the run |

Layers, lowest first: the profile's `vars`, the template's, the flow's, the PR's, then the router's own (`PR`,
`STATE`, `DEF_DIR`, `KIT`, `CHECKS`, and `BASE_BRANCH` from `base`), which no `vars` may set. A name nobody gives that
the template declares makes the PR wait (`flow show` says `waiting for: WT`); a name nobody declares is a typo and is
refused.

## 5. Show the file, then dry-run it

Show the whole file. Then check it against the run's state when the run exists, else against a throwaway one:

```sh
export ROUTER_STATE=$SCRATCH/router                      # the run's state; a dry run writes no version
# no run yet: export ROUTER_STATE=$(mktemp -d)           # a throwaway state, so the check reads no other run
$R flow apply <file> --dry-run
```

| It prints | Do |
|---|---|
| `OK flow v<N> -> v<N+1>, dry run, nothing written` and the change lines | read the change lines to the owner; the file is valid |
| `NOT OK flow: <n> problem(s), nothing changed` and one line per problem | fix each line with the owner, show the diff, run the dry run again |

The problem lines name their layer (`vars.X`, `A1: vars.X`, `template inner-pr: vars.X`, `profile python: vars.X`)
and their PR. Common ones: `base main: …never main or master`, `after names p9, which is not in the flow`, a cycle in
`after`, an unknown template name, `{X} is not a variable`, `slots` that is not a positive integer.

**Never write a file the dry run rejects.** Loop until it prints `OK`. If the owner wants to stop with problems left,
keep the draft in the conversation, write nothing, and say which lines still fail.

## 6. Write it, and hand over

Write the file where the owner said, then print the next command and stop:

```sh
$R flow apply <file> --by <owner> --note "first plan"     # from the coordinator's Orca terminal, after router.py init
```

Under `start: auto` that apply starts every ready PR: say so before the owner runs it. Remind them what the run
directory must hold before the first chain: `$SCRATCH/intent/<ISSUE>.md` per PR, the worktrees, and any file the
profile names (FLOWS.md, "What the run provides").

## Rules

- No worker, no chain, no `flow apply` without `--dry-run`, no `flow start`, no `router.py init`. This skill writes
  one file.
- Nothing from the repository goes into the flow file but paths and issue numbers; no secrets.
- A later change to an applied flow is a new version: edit the file, dry-run it, and let the owner apply it. A
  settled step, a started PR's `after`, or a variable a settled step used cannot change.
