# Codex support: shared source, explicit runtime boundaries

Decision and source review, 2026-09-10. Scope: the missions plugin.

## Repositories inspected

The links below pin the actual source revisions inspected, rather than search-result
summaries of earlier installation designs.

| Repository | Observed design | Application to missions |
|---|---|---|
| [obra/superpowers, b36e082](https://github.com/obra/superpowers/blob/b36e0829c6d0140e93cfef2ca599b1b07d4a7797/.codex-plugin/plugin.json#L23-L24) | Native Claude and Codex manifests consume the same `skills/` tree. | Add packaging metadata without copying or generating skill bodies. |
| [EveryInc/compound-engineering-plugin, 16c2b97](https://github.com/EveryInc/compound-engineering-plugin/blob/16c2b9721e5cea3bdc64ba50ffcff72d912fd948/.codex-plugin/plugin.json#L20) | Native Codex installation loads the canonical skills; its [marketplace](https://github.com/EveryInc/compound-engineering-plugin/blob/16c2b9721e5cea3bdc64ba50ffcff72d912fd948/.agents/plugins/marketplace.json) points at the plugin root. | Keep docs, agents, scripts and driver beside the shared skills in one plugin package. |
| [vercel-labs/skills, 80feb48](https://github.com/vercel-labs/skills/blob/80feb48868972d518436f26711509bc78595b5cb/README.md#L124-L131) | Its installer recommends one canonical copy with links from agent directories. | Linking can support development, but a second manually maintained skills tree is unnecessary. |

Superpowers loads runtime-specific guidance from one
[platform reference](https://github.com/obra/superpowers/blob/b36e0829c6d0140e93cfef2ca599b1b07d4a7797/skills/using-superpowers/SKILL.md#L52-L59).
Compound Engineering keeps specialist instructions in
[shared prompt assets](https://github.com/EveryInc/compound-engineering-plugin/blob/16c2b9721e5cea3bdc64ba50ffcff72d912fd948/skills/ce-plan/references/research.md#L7-L9),
then passes them to the available generic subagent mechanism. Both separate workflow
content from runtime mechanics.

## Chosen structure

- `plugins/missions/skills/` remains the only authored skills tree. Claude slash
  commands and Codex skill invocation select the same `SKILL.md` files.
- `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` describe the same
  plugin root; the Codex marketplace lists missions only.
- Every mission skill loads [one shared runtime reference](RUNTIMES.md) before executing its
  workflow. That reference owns invocation mapping, bundled-path resolution,
  delegation differences and unsupported operations.
- Existing agent prompts, mission schemas, shell helpers and Python driver remain
  shared. Worker and reviewer dispatch briefs live in `skills/mission-run/references/`;
  both the skill and driver read them. Routine instruction changes require one edit,
  without a conversion step. The deterministic state machine remains Python code;
  changing its algorithm still requires implementation and behavioral tests.
- Release validation checks shared manifest metadata and runtime-reference coverage
  so packaging differences do not become another manual synchronization task.

Prefer native plugin installation over a generic skill-only installer: missions
skills reference sibling docs, agents and driver code. The Vercel installer
[copies individual skill directories](https://github.com/vercel-labs/skills/blob/80feb48868972d518436f26711509bc78595b5cb/src/installer.ts#L337-L361),
which does not establish that those sibling resources will be installed.

Bundled paths must resolve from the loaded skill's location, not the user's working
directory or an assumed `CLAUDE_PLUGIN_ROOT` environment variable. Compound
Engineering documents this recurring portability failure in its
[path-resolution guidance](https://github.com/EveryInc/compound-engineering-plugin/blob/16c2b9721e5cea3bdc64ba50ffcff72d912fd948/AGENTS.md#L310-L336).
Marketplace installs can also cache a snapshot; they should not be described as
live links to an edited checkout ([development precedent](https://github.com/EveryInc/compound-engineering-plugin/blob/16c2b9721e5cea3bdc64ba50ffcff72d912fd948/AGENTS.md#L20-L31)).

## Execution and enforcement

Codex `mission-run` uses the existing `bin/missions` driver with the Codex adapter.
The driver owns serial dispatch, locks, budget checks, worker grading, validation
and durable resume state. It does not depend on translating Claude `Agent` calls
or assuming that a Codex subagent inherits Claude's agent tools and hook context.
Shared planning, inspection and amendment workflows use the active runtime's tools.

Claude hook registration moves from the automatically discovered `hooks/hooks.json`
to `hooks/claude.json`, explicitly registered in the Claude manifest. This avoids
Codex accidentally loading Claude-specific guards: official
[OpenAI hooks documentation](https://learn.chatgpt.com/docs/hooks#plugin-bundled-hooks)
confirms that Codex discovers a plugin's `hooks/hooks.json` even without a manifest
entry. Superpowers encountered the same issue and
[tests an explicit empty hooks object](https://github.com/obra/superpowers/blob/b36e0829c6d0140e93cfef2ca599b1b07d4a7797/tests/codex/test-marketplace-manifest.sh#L55-L72).
Missions uses a nondefault Claude hook filename instead, keeping the Codex manifest
within the fields accepted by its packaging validator.

This is not hook parity. Codex's interactive planning and editing do not acquire
the Claude plugin's per-tool enforcement; the supported execution path uses the
driver's existing controls. User-installed Codex hooks remain separate. The driver
still hands off at the terminal PR phase; it must not report mission completion
merely because implementation and milestone validation finished.

## Follow-ups

- Reverse-vendor crosscheck: the current `mission-crosscheck` launches Codex to
  critique a Claude-authored plan. Running it from Codex would lose the promised
  vendor independence. Until a Claude reviewer adapter and transcript audit exist,
  the Codex route reports this limitation instead of treating Codex-on-Codex as a
  cross-vendor review. Tracked in [#28](https://github.com/dimakrest/context-engineering/issues/28).
- Driver PR phase: [#30](https://github.com/dimakrest/context-engineering/issues/30) tracks
  the missing driver tail. [#10](https://github.com/dimakrest/context-engineering/issues/10)
  remains related authority/spend work; shared skills do not implement the driver phase.
- Native Codex mission guards: [#29](https://github.com/dimakrest/context-engineering/issues/29)
  requires an event/payload compatibility layer and behavioral tests before claiming
  interactive enforcement parity.
- Existing related work remains in [#19](https://github.com/dimakrest/context-engineering/issues/19)
  (driver status), [#7](https://github.com/dimakrest/context-engineering/issues/7) (resume/quota),
  and [#23](https://github.com/dimakrest/context-engineering/issues/23) (remaining CI layers,
  required checks and optional paid smoke). This change adds automatic offline regressions;
  it does not close those broader issues.
