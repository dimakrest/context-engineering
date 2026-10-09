# flows: the Orca router

A Claude Code plugin for running a flow of inner PRs through Orca workers. Its core is the router: scripts that
sit between an Orca Run's mailbox and the coordinator, and no model runs in them. They release finished workers,
start each PR's next step, run its checks, collect every worker's logs, and ring the coordinator only when
something needs a decision: a gate, a failure, a check that is not OK, a worker's question.

```sh
/plugin install flows@dimakrest-context-engineering
```

| Skill | Does |
|---|---|
| `/flows:flow-plan` | writes a flow file with the owner from an issue or a checklist, and checks it with `flow apply --dry-run` |
| `/flows:flow-run` | the coordinator's loop: bind the Run, apply the flow, open the page, wait, rule on each ring |
| `/flows:flow-status` | where a run is: the PRs, the page's URL, live workers, the logs collected of those settled |
| `/flows:flow-report` | the run report: where the time, the tokens and the interruptions went, and the lessons |

## The docs

- [`docs/FLOWS_GETTING_STARTED.html`](docs/FLOWS_GETTING_STARTED.html): the first run, on the smoke template, as it
  ran on real Orca. Open it in a browser.
- [`docs/FLOWS.md`](docs/FLOWS.md): the reference: the flow file, the roles and specs, profiles, the page, the
  collector, the report, the coordinator's loop, environment variables, troubleshooting, and how it was tested.
- `router/router.py --help`: every command and every file under the state directory, from the code.

## What is in it

| Path | What |
|---|---|
| `router/router.py` | the mailbox daemon, the chain runners, the scheduler, the page server and every command |
| `router/collector.py`, `router/report.py`, `router/progress.py` | the logs, the run report, the owner's static view |
| `router/page/` | the live page: one HTML file, its script and styles; no build step |
| `router/templates/`, `router/specs/`, `router/checks/` | the chain templates (`inner-pr`, `smoke`), the role specs, the one-line checks |
| `router/profiles/` | what differs between repositories: `python`, `typescript`, `bell` |
| `skills/` | the four skills |
| `evals/flow-plan/` | a `claude plugin eval` case for `/flows:flow-plan` |
| `tests/` | the suites, `fake-orca` (a stand-in for the Orca CLI) and the fixtures |

## Tests

```sh
cd plugins/flows
python3 tests/test_router.py            # the suite, against tests/fake-orca; prints its count. -k <Class> runs one class
python3 tests/test_page.py              # the page in headless Chromium; skips every test without Playwright
python3 -m pip install playwright && python3 -m playwright install chromium   # once, for test_page.py
```

From the repository root:

```sh
claude plugin validate plugins/flows --strict
claude plugin eval plugins/flows --allow-tools Bash Write Edit --runs 1   # the flow-plan eval; it runs Claude
```

The eval grants Bash, and `claude plugin eval` refuses a Bash-granting case on a machine whose credential files it
cannot fence off (an AWS `credential_process`, for one). There, `python3 tests/test_router.py -k Skills` checks the
skills instead, and `evals/flow-plan/verify.sh <run dir>` re-runs the dry run on a kept run's flow file.

Python 3.9+, standard library only. The suites need no network and no real Orca. Every assertion's history, and
what is not tested yet, is in [FLOWS.md](docs/FLOWS.md#tested-how-history).
