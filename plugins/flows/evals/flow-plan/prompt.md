---
name: flow-plan-three-items
description: flow-plan turns a three-item checklist into a flow file that router.py flow apply --dry-run accepts
max_turns: 40
timeout_seconds: 900
allowed_tools: [Skill, Read, Write, Edit, Bash, Glob, Grep]
runs: 1
---
/flows:flow-plan

Plan a flow from the issue below. You are the owner too, so answer your own questions with these facts and do not
wait for anyone:

- The repository is a Python project tested with pytest (it has pyproject.toml and pytest.ini): use the python profile.
- The run directory is the directory `run` under the current working directory (create it). Use the absolute path.
- Every PR merges into the branch `payments-v3`. Each PR's worktree is `{SCRATCH}/wt/{PR}`.
- Two slots, start manual.
- Write the flow file to `flow.json` in the current working directory.
- After the dry run accepts the file, save the dry run's whole output to `dryrun.txt` in the current working
  directory (`... --dry-run > dryrun.txt 2>&1`).

The issue (#40, "Move the payments client to v3"):

> The payments client moves from v2 to v3. Sub-issues:
>
> - [ ] #41 Pin the v3 client next to v2 (dependency only, no code to mutate)
> - [ ] #42 Switch the checkout flow to the v3 client (needs #41)
> - [ ] #43 Remove the v2 client and its adapter (needs #42)
