# Contract <PR> · base <full sha of the PR's base commit> · intent: #<issue>, "For the agent" comment of <date>

Written by the contract author at the base commit above. Frozen at acceptance: its sha256 goes into every later brief. It changes only by an entry under "Amended".

## Branch

<Fact-dependent PRs only. The fact, the probe that measured it (command, last line), and the branch the intent's rule picks from it. Otherwise: "not fact-dependent".>

## Rows

| id | condition | observable that proves it | kind | evidence |
|---|---|---|---|---|
| R1 | <what must be true when the PR is done> | <a test node id, a command and its expected last line, or a grep that must return nothing> | red-first / green-pinned / gate | run: `<command>` → `<last line>` on base, or read: `<file:line>` plus why it could not be run |

Every item of the intent's goal and "Done means" for this PR maps to at least one row. No row falls outside them.

## Must not

| item (from the intent's "Must not change") | the test or check that guards it | its result on base |
|---|---|---|

## Names

<Every seam, fixture, class, function and test path the rows use, each by name, with its `file:line` at the base commit. The name is what the later steps work from; the line only says where it sat at this base.>

## Scope

- Test writer may touch: <paths>
- Implementer may touch: <paths>
- Untouched by both: the test runner's settings file, the frozen paths the profile names, <others>

## Gates

| command (as the profile's TEST_CMD runs it) | result on base |
|---|---|

## Fixed and Free

- Fixed: outcomes, order, paths, gates.
- Free: helper names, fixture wiring, <others>.

## OPEN

<"none", or one entry per design conflict: the options, a recommendation, and what undoing each would cost. A branch the intent reserves for the owner goes here too.>

## Amended

<Appended only: date, ruling, what changed, why. Empty at acceptance.>
