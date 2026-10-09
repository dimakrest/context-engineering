---
name: flow-report
description: Build and read back the run report of a flow run by the Orca router - run `router.py report` (collecting the workers' logs first with `collect --all` when logs/ is missing or incomplete, and saying so), open report.html in an Orca tab, and read the lessons table back in a few lines, each with the record it comes from. Changes no state of the run. Use when the owner asks "how did the run go", "where did the time go", "/flows:flow-report", or when a flow's chains are done and the next run is being planned.
---

# /flows:flow-report — the run in numbers, and its lessons

The report is one self-contained page per run, `report.html` in the state directory, built from the router's
records alone: no Orca call, no session file opened, no transcript text. A number whose record is missing says
`not recorded`; nothing is estimated. This skill builds it, opens it, and reads its lessons back.

```sh
R=${CLAUDE_PLUGIN_ROOT}/router/router.py
```

Set `ROUTER_STATE` (or `SCRATCH`; the state is then `$SCRATCH/router`) so the commands find the run.

## 1. Are the logs collected?

```sh
$R status                 # the "logs N/M settled dispatches collected" line
```

The mailbox daemon collects each released worker's logs into `logs/` on its own. When `logs/` is missing (a run whose
daemon was not collecting, or `ROUTER_COLLECT=0`) or `N` is less than `M`, collect first, and **say that you did and
why** before anything else:

```sh
$R collect --all          # a collected dispatch is skipped; prints one summary line
```

It ends with `OK collected <n> · skipped <n> · locked <n> · not settled <n> · failed <n>`. `locked` is another
collector at work (the daemon's), not an error. `not settled` is a worker whose attempt has no end yet. `failed`
leaves a `collector: <dispatch>: <why>` line in `journal.md`: name those dispatches to the owner. Collecting writes
only under `logs/`; it never touches a chain.

`$R collect --dry-run` prints each dispatch's session match (`unique`, `ambiguous`, `none`) and the counts, and copies
nothing. A dispatch whose match is not `unique` has no `tokens.json`, and the report counts its tokens as not
recorded.

## 2. Build the report

```sh
$R report --metrics $ROUTER_STATE/metrics.json      # writes $ROUTER_STATE/report.html, prints its path
$R report --pr <pr> --out <file>                    # one PR: its section, and the header and metrics count it alone
```

It prints `OK report <path> · <n> PRs · <n> dispatches (<n> ad hoc) · <n> collected sessions`. Report that line.

## 3. Open it

When Orca is reachable (`orca status` answers), open the page in an Orca browser tab:

```sh
orca tab create --url file://$ROUTER_STATE/report.html
```

Otherwise give the path and say the owner can open it in any browser: it requests nothing from the network.

## 4. Read the lessons back

Read `lessons` from the metrics file (or section g of the page) and say each in one line, with its record:

| Lesson | What it is | Its record |
|---|---|---|
| slowest step | the PR step with the longest sum of attempts | `chains/<pr>/state.json steps[<step>].attempts` |
| most-retried step | the step with the most attempts | the same, with its dispatches |
| role that asked the most questions | the spec whose workers asked most | `journal.md question lines` |
| longest wait at a gate (raw) | one gate's wait from `paused: blocked` to the coordinator's line: how long the owner took to answer | `journal.md paused: blocked at … .. coordinator at …` |

The gate lesson is raw on purpose. Section a's time split puts each second of a PR's wall clock in one part only:
worker time (some worker ran), then at gates, then paused, then the rest, each part zero or more and the four adding
up to the wall clock. So "at gates" there is the gate wait less the time workers ran, and can be smaller than the
lesson's number.

A lesson with no record says so (`no step ran twice`, `no journal.md`). Without `journal.md`, gate waits, pauses, the
rest, the ring kinds (except silent ones, counted from `wake/` file names), checks not OK and questions say not
recorded; the wall clock and the worker time still come from `state.json`. An empty `journal.md` is a real zero.

Then, in two or three lines, what the numbers say about the next plan (for `/flows:flow-plan`): a step that is slow
because it waited on the owner, a role that keeps asking the same thing, tokens that went to sessions with no match.

## Rules

- Read-only for the run: no `resume`, `retry`, `reply`, `flow apply`, `stop`. Only `collect` (into `logs/`) and
  `report` (its own files) write, and neither changes a chain.
- Quote no transcript text. The page and the metrics hold none; keep it that way in the reply.
- Prices are the owner's: `--prices <file>` gives costs, and without it every cost is `not priced`. Do not invent one.
