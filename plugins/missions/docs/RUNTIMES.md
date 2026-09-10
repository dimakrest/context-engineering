# Missions runtime guide

Read the section for the current host before following a mission skill. The files in
`skills/`, `agents/`, `templates/` and `scripts/` are shared sources; runtime differences
live here. This guide changes how a workflow runs, not its contract or evidence rules.

## Paths and commands — both hosts

The shared text — skills, agents, templates and hook messages — writes the installed plugin
root as `${CLAUDE_PLUGIN_ROOT}`. That is the one spelling; nothing in the shared text is
written against another variable name.

In Claude Code the host substitutes `${CLAUDE_PLUGIN_ROOT}` in a plugin skill's content before
the session reads it, and exports it to hook processes; it is not set in the session's own
shell, so a shell call must use the substituted absolute path, never the bare variable.

In Codex nothing substitutes it. Resolve it from the **loaded skill's absolute path**: the
plugin root is two directories above the `SKILL.md`'s directory (`skills/mission-*/SKILL.md`
→ plugin root). Do not assume the target project contains this repository, and do not search
for the newest cache version. Use that absolute path in place of `${CLAUDE_PLUGIN_ROOT}` in
every shell call and in every reference that shared text or hook output prints — for example
`Schema: ${CLAUDE_PLUGIN_ROOT}/templates/MISSIONS_TEMPLATES.md` names a file under the
resolved root.

```bash
# Codex substitutes the resolved absolute plugin root for "${CLAUDE_PLUGIN_ROOT}".
bash "${CLAUDE_PLUGIN_ROOT}/scripts/mission-state.sh" .missions/<slug>
```

Shell variables may not persist between tool calls; write the absolute path into each call
rather than relying on an earlier export. Keep the working directory at the **target
project's checkout**; `.missions/<slug>` belongs there, not in the installed plugin. Read the
target project's `AGENTS.md`, `CLAUDE.md` and other applicable instructions.

`/missions:mission-plan` and similar references in the shared text name skills. In Claude,
invoke them as written. In Codex, select the installed skill with `/skills` or `$missions:mission-plan`
(installed plugin skills use the `missions` namespace). Continue by reading the corresponding
`skills/mission-*/SKILL.md`; do not send Claude slash commands to a shell. User arguments
are the mission slug, mode and constraints supplied with the invocation.

## Claude Code

Follow the shared workflow's Agent calls, model seats and review commands. The Claude
manifest explicitly registers `hooks/claude.json`; that file connects the existing guards
to Claude events.

## Codex

### Planning, design, amendment and research

Follow the shared skills. For `mission-researcher` dispatches, read `agents/mission-researcher.md`
and give its Markdown body plus the bounded assignment to a Codex subagent. Resolve
`${CLAUDE_PLUGIN_ROOT}` references in that body to the installed plugin root first; the same
applies to any shared agent body or template the session reads. Use the host's
available delegation tools; the Claude `Agent` tool, `subagent_type`, model names and YAML
`tools` frontmatter are not Codex configuration. If delegation is unavailable, perform the
bounded read-only lookup in the current session. Read-only is a task constraint here, not
a claim that the role's Claude tool allowlist is enforced by Codex.

Probe connected tools through the current host. Run `mission-plan`'s codebase-intelligence
probe snippet as written, with `codex mcp get <name>` in place of `claude mcp get <name>`; a
registered server is not proof that it is connected, and only a connected one earns `+mcp`.
Record only available capabilities in the mission's standing constraints.

Claude `Seat` fields stay Claude-only. For Codex driver model overrides, use
`driver.json` → `roles.<role>.model`; `null` uses the CLI's configured default. Do not put
Codex model names into Claude seat fields or invent equivalent model/effort mappings.

### Run and resume

**Use `bin/missions` for implementation and milestone validation.** The Claude session
dispatch loop in `mission-run` is the shared description of the algorithm; Codex delegates
that loop to the existing driver. Do not launch native Codex worker/validator subagents as
a replacement. The driver already reads the shared agent bodies and judgment rules.

1. Read the mission digest explicitly at entry and after compaction. There is no missions
   SessionStart hook in Codex. For resume, follow `mission-resume`'s file/git reconciliation,
   but do not rewrite state or remove locks while a driver is running. The driver owns its
   `.driver.lock`, `.writer`, `.lease`, handoff grading and interrupted-run recovery.
2. Require the contract, features and design. Run
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/check.sh" .missions/<slug>`.
   Work on the branch named by `state.md`, reconcile unknown dirty work, and use the shared
   halt rules. Do not edit the phase by hand: on an authorized first run the driver itself
   moves `planning` → `implementing` and journals that decision once preflight passes.
3. If `driver.json` is absent, initialize it with `--harness codex`. Preserve an existing
   config and its caps; check its `harness` before dispatch. A different harness requires
   an explicit choice, not an `init --force` that discards configuration.
4. Run preflight and a dry run, then execute the authorized run:

   ```bash
   # Codex substitutes the resolved absolute plugin root for "${CLAUDE_PLUGIN_ROOT}".
   bash "${CLAUDE_PLUGIN_ROOT}/bin/missions" init .missions/<slug> --harness codex
   bash "${CLAUDE_PLUGIN_ROOT}/bin/missions" preflight .missions/<slug>
   bash "${CLAUDE_PLUGIN_ROOT}/bin/missions" run .missions/<slug> --dry-run
   bash "${CLAUDE_PLUGIN_ROOT}/bin/missions" run .missions/<slug>
   ```

   The `init` line is only for a missing config. Do not dispatch after failed preflight.
   Keep the process alive while it runs, report progress from its files, and wait for its
   exit; an empty output interval is not completion. Never start a second driver to poll.
5. Read the exit reason and `resume_next`. The [driver exit table](../README.md#running-a-mission--two-ways)
   defines the continuation: a limit stop is resumable; budget, authority, contract,
   provider-quota and gate stops need their stated resolution. Do not automatically raise
   caps, weaken the sandbox, clear halts or retry the same provider failure.
6. Exit `0` after the last milestone means **validation finished**, not that a PR exists.
   Verify the terminal prerequisites in `mission-run`, then follow its **Terminal steps**
   in the Codex session, including `mission-pr-review`. If already in `pr`, resume that
   review directly. A mission already in `done` needs only a status report. The driver
   does not implement the terminal PR phase; do not feed `pr` back into its run loop.

The driver provides serial dispatch, locks, caps, blind review preparation, Git hooks and
post-exit grading. It does not make a sandbox bypass safe. No missions hooks are installed
for native Codex session tools; planning and terminal review constraints are instructions,
not automatic hook blocks. User/project Codex hooks may still run independently.

### Spend and status

Codex driver `cost` events with `unit: tokens` are measured token usage. Aggregate by task
from the journal; preserve the distinction from estimates and from Claude USD events.
Codex has no measured USD total or per-run dollar-budget enforcement in this adapter.
Show its USD spend as **unknown**, including for mixed-harness missions; a measured Claude
subtotal is not the mission total. Do not overwrite `spend_usd` with an invented conversion
or zero. Dispatch, wall-clock, repair and timeout limits still apply to driver runs.

### Terminal PR review

Follow the shared `mission-pr-review` sequence and progress file. Where `/simplify` or
`/code-review` is unavailable, perform the specified quality pass with available Codex
tools, then delegate a fresh whole-branch correctness review. If subagents are unavailable,
use a separate `codex exec --sandbox read-only` review with only the review brief and repository evidence.
Record which path ran. The quality pass may edit code before review; the review and finding
assessment remain read-only. Run the repository's adversarial review only if available,
and report its absence. Preserve the draft PR, evidence references, finding dispositions
and human merge boundary. These session reviews are outside the driver's dispatch caps;
report that separately and respect the mission's terminal-review budget.

### Cross-vendor review

`mission-crosscheck` currently implements **Claude-authored plan → Codex reviewer** and
audits Codex text transcripts. In a Codex session, stop before launching that workflow and
report that the reverse **Codex → Claude** adapter is unavailable. A second Codex instance
does not satisfy vendor independence. Preserve sealed packages and unfinished progress;
do not mark the crosscheck passed or silently replace it with a same-vendor review.
Other mission skills can proceed when crosscheck is optional; when the user requires it,
record the unresolved prerequisite. See [design and follow-ups](CODEX_DESIGN.md).
