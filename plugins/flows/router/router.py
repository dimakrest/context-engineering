#!/usr/bin/env python3
"""Orca router: scripts between an Orca Run's mailbox and the coordinator. No model runs in here.

Why: in the last Orca run every worker_done woke the coordinator, which settled it by hand (read, release, ack,
journal, start the next worker). This does those steps without a model and rings only when a decision is needed.

  mailbox daemon   The only consumer of the Run's mailbox. A heartbeat becomes a last-seen time per worker. Every
                   other message becomes one file under events/. A settled worker's terminal is released, then
                   the delivery is acknowledged. Because it always waits, Orca types no nudge into the
                   coordinator's terminal.
  chain runner     One per inner PR. Starts each step's worker, waits for its worker_done, runs the step's checks
                   (one line each), goes on. It pauses and rings when a worker fails, cannot start, a check is
                   not OK, a script step fails, or the step is a gate (contract acceptance, merge).
  doorbell         `router.py wait`. Blocks until something needs the coordinator, prints one screen, exits. The
                   coordinator starts it in the background as the last action of every turn.

Usage (the coordinator's whole interface; every command but progress prints a few lines at most):
  router.py init [--run <run_id>]                 record the bound Run, start the mailbox daemon
  router.py chain <pr> --def <chain.json> [K=V ...] [--dry-run]
                                                  create one PR's chain from a definition and start its runner
  router.py wait [--timeout-min <n>]              the doorbell
  router.py resume <pr> [--accept "<why>"] [--from <step>] [--note "<text>"] [--adopt <dispatch_id>] [--set K=V]
                                                  go on after a gate; re-run the checks of a step whose check was
                                                  not OK; --accept overrides a failed step; --from re-runs from a step;
                                                  --adopt takes over a worker whose start receipt was lost; --set
                                                  gives a variable its value
  router.py retry <pr> [--note "<text>"] [--agent a] [--model m] [--effort e]
                                                  run the failed step again with a fresh worker
  router.py fail <pr> [--step <id>] --why "<text>"
                                                  write off a running step whose worker ended without worker_done;
                                                  refused unless Orca shows that worker stopped, failed or exited
  router.py reply <message_id> "<answer>"         answer a worker's question (its chain keeps running)
  router.py worker <label> (--spec "<text>" | --spec-file <f>) --agent <a> [--model m] [--effort e]
                   [--worktree <selector>] [--pr <pr>]
                                                  an ad hoc worker (arbiter, triage of a failure); rings when done
  router.py status                                one screen: daemons, chains, live workers, unanswered questions
  router.py last [<n>]                            print the last n rings again (default 1)
  router.py workers                               one line per worker Orca still accounts for
  router.py progress                              the owner's view, not the coordinator's: every inner PR of the
                                                  plan, the step each open chain is at, the worker that runs it.
                                                  The daemons keep the same view in progress.html
  router.py plan <plan.json>                      record the run's inner PRs in order, so that progress also shows
                                                  those not started: {"title": "…", "prs": [{"id", "title", "part"}]}
  router.py stop [<pr> | --all]                   stop a chain's runner, or every daemon
  router.py page [--open]                         the URL of the page the mailbox daemon serves on 127.0.0.1: the
                                                  live view of the run and its flow, edited on a canvas. --open
                                                  opens it in an Orca browser tab (orca tab create --url <url>)
  router.py collect [--all | --pr <pr> | --dispatch <id>] [--force] [--dry-run]
                                                  gather settled workers' logs now (collector.py): for a run whose
                                                  daemon was not collecting, or after a fix. --dry-run prints each
                                                  dispatch's session match and copies nothing

The flow (one versioned file that is the run's plan: its PR graph, each PR's steps, the variables):
  router.py flow apply <file> [--base <version>] [--by <who>] [--note "<text>"] [--dry-run]
                                                  the only way a flow enters or changes a run: v1, then v+1 with one
                                                  history row. Refused, nothing changed, when --base is not the
                                                  current version, or a change touches a settled step, a started PR's
                                                  after, or a variable a settled step used. --dry-run: the same
                                                  checks and change lines, nothing written. The page's Apply (POST
                                                  /flow) runs this same apply
  router.py flow show                             the graph: one line per PR, with its state or what it waits for
  router.py flow start <pr>                       start a PR now (under "start": "manual"); refused unless it is ready
  router.py flow history [<n>]                    the last n versions: who, when, why, what changed (default 10)
  With a flow, chain refuses the PRs the flow names and plan is refused: the flow is the plan.

Env:
  ROUTER_STATE          state directory (default $SCRATCH/router)
  ORCA_CLI_COMMAND      the Orca CLI (default orca)
  ROUTER_WAIT_MS        one mailbox long-poll (default 120000)
  ROUTER_SILENT_MIN     minutes without a heartbeat before a live worker rings as silent (default 20)
  ROUTER_START_TIMEOUT_MS  worker-start --timeout-ms (default 300000)
  ROUTER_MAX_CHAINS     chains allowed to run at once (default 2); a flow's "slots" replaces it
  ROUTER_POLL_S         file poll interval (default 3)
  ROUTER_REGISTRY_WAIT_S   how long a message waits for its worker's start receipt to be recorded (default 10)
  ROUTER_PORT           the page's port on 127.0.0.1 (default 0: a free port, written to page.json)
  ROUTER_COLLECT        0 turns off the automatic collection of a released worker's logs (default 1: on)
  FLOWS_CLAUDE_PROJECTS where Claude Code writes its session files (default ~/.claude/projects)
  FLOWS_CODEX_SESSIONS  where Codex writes its session files (default ~/.codex/sessions)

Exit codes: 0 done; 1 a refusal or a failed Orca call (one line says why); 2 bad usage; 3 the doorbell found a
dead daemon (the line starts with ACT).

Files under the state directory:
  run.json                    the Run and the coordinator terminal the daemons act for
  mailbox.pid, mailbox.log    the daemon
  events/<dispatch>/*.json    every message that is not a heartbeat
  liveness/<dispatch>         last heartbeat: time and phase
  dispatches/<dispatch>.json  which chain and step started a worker
  chains/<pr>/state.json      the chain: its frozen steps, their attempts, the variables
  wake/*.txt, wake/seen/      what the doorbell prints, then what it has printed
  journal.md                  one line per settled event, append-only
  plan.json                   the run's inner PRs, from `router.py plan`
  flow.json                   the flow, the router's copy: the latest version, its history, the templates and the
                              profile as they were read when it was applied
  flow.lock                   taken by flow apply, a scheduler pass and a runner's re-read, so they take turns
  chains/<pr>/def.json        a flow PR's own steps, when it overrides its template's
  progress.html               the owner's view. Rewritten at every change, and it reloads itself in the browser
  page.json                   where the mailbox daemon serves the page: {url, host, port, pid}; gone when it stops
  logs/<pr>/<step>/<dispatch>/  a settled worker's logs and its agent session file; logs/index.jsonl, one row each
                              (collector.py --help). Written by a collector the daemon starts after each release

The page (served by the mailbox daemon from a thread; 127.0.0.1 only; the mail loop never waits for it):
  GET /            the page (router/page/), GET /page/<file> its script and styles
  GET /state       what progress shows, as JSON, plus the flow: {version, slots, start, prs, palette, ...}
  GET /flow        the router's copy of the flow (flow.json)
  POST /flow       {"base": <version>, "by", "note", "flow": {...}, "dry_run"?}: `flow apply`. 200 {ok, version,
                   changes}; 409 {reason: "stale: the run is at v<N>", current}; 422 {problems}, nothing changed
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import hashlib
import http.server
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORCA = os.environ.get("ORCA_CLI_COMMAND", "orca")
WAIT_MS = int(os.environ.get("ROUTER_WAIT_MS", "120000"))
SILENT_MIN = float(os.environ.get("ROUTER_SILENT_MIN", "20"))
START_TIMEOUT_MS = int(os.environ.get("ROUTER_START_TIMEOUT_MS", "300000"))
MAX_CHAINS = int(os.environ.get("ROUTER_MAX_CHAINS", "2"))
POLL_S = float(os.environ.get("ROUTER_POLL_S", "3"))
REGISTRY_WAIT_S = float(os.environ.get("ROUTER_REGISTRY_WAIT_S", "10"))
COLLECT = os.environ.get("ROUTER_COLLECT", "1") != "0"
COLLECT_WAIT_S = 3600   # how long a collector waits for the chain to record the end of the attempt and its checks

DONE = ("done", "skipped")
PAUSED = ("failed", "check_failed", "start_failed", "start_unknown", "script_failed", "blocked")
RELEASED_OK = ("released", "already_released", "retained", "release_pending")
VAR_RE = re.compile(r"(?<!\$)\{([A-Z][A-Z0-9_]*)\}")   # {NAME}; a shell ${NAME} is left alone
# Known only while a step runs; a spec is rendered before its worker starts, so it may use the first three only.
STEP_VARS_EARLY = ("STEP", "ATTEMPT", "HEAD_BEFORE")
STEP_VARS_LATE = ("TASK", "DISPATCH", "REPORT", "HEAD_AFTER")


class RouterError(Exception):
    """A problem that fits one line. A command prints it and exits 1; a runner rings with it and pauses."""


def die(msg: str, code: int = 1) -> "None":
    print(msg, file=sys.stderr)
    sys.exit(code)


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(ts: str) -> float:
    try:
        return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0.0


def oneline(text: object, limit: int = 240) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


class State:
    def __init__(self) -> None:
        root = os.environ.get("ROUTER_STATE") or (
            os.path.join(os.environ["SCRATCH"], "router") if os.environ.get("SCRATCH") else ""
        )
        if not root:
            die("set ROUTER_STATE, or SCRATCH (the run directory)", 2)
        self.root = Path(root)
        for d in ("events", "liveness", "dispatches", "chains", "wake/seen", "wake/keys", "replied"):
            (self.root / d).mkdir(parents=True, exist_ok=True)

    def __truediv__(self, other: str) -> Path:
        return self.root / other


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")   # the page server writes from a thread
    tmp.write_text(text)
    os.replace(tmp, path)


def write_json(path: Path, obj: object) -> None:
    atomic_write(path, json.dumps(obj, indent=1) + "\n")


def read_json(path: Path) -> "dict | None":
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def append(path: Path, line: str) -> None:
    with open(path, "a") as f:
        f.write(line.rstrip("\n") + "\n")


def journal(S: State, pr: str, step: str, task: str, dispatch: str, text: str) -> None:
    append(S / "journal.md", f"{now()} | {pr or '-'} | {step or '-'} | {task or '-'} | {dispatch or '-'} | {oneline(text, 400)}")


def wake(S: State, kind: str, pr: str, lines: "list[str]", key: str = "") -> None:
    """Queue one screen for the doorbell. A key makes it idempotent (a replayed delivery rings once)."""
    if key:
        marker = S / "wake" / "keys" / safe(key)
        if marker.exists():
            return
        atomic_write(marker, now() + "\n")
    body = "\n".join(oneline(x, 1600 if i == 1 and kind == "question" else 300) for i, x in enumerate(lines[:12]))
    atomic_write(S / "wake" / f"{time.time_ns()}-{safe(pr or 'run')}-{kind}.txt", body + "\n")


# ---------------------------------------------------------------- Orca CLI

def orca_env(S: State) -> "dict[str, str]":
    """The daemons act for the coordinator's terminal: the two variables Orca resolves the caller from."""
    env = dict(os.environ)
    run = read_json(S / "run.json") or {}
    if run.get("terminal"):
        env["ORCA_TERMINAL_HANDLE"] = run["terminal"]
    if run.get("pane"):
        env["ORCA_PANE_KEY"] = run["pane"]
    return env


_child: "subprocess.Popen | None" = None


def orca(S: State, args: "list[str]", timeout: "float | None" = None, caller: bool = False) -> "tuple[dict | None, str]":
    """Run one Orca CLI command with --json. Returns (parsed answer or None, raw text for the log).
    caller=True speaks as the terminal that runs this command, not as the one recorded in run.json."""
    global _child
    try:
        _child = subprocess.Popen(
            [ORCA] + args + ["--json"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
            env=dict(os.environ) if caller else orca_env(S)
        )
        out, err = _child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _child.kill()
        out, err = _child.communicate()
        return None, f"timed out after {timeout}s"
    except OSError as e:
        return None, f"cannot run {ORCA}: {e}"
    finally:
        _child = None
    for text in (out, "\n".join(l for l in err.splitlines() if "_keepalive" not in l and "_heartbeat" not in l)):
        i = text.find("{")
        if i >= 0:
            try:
                return json.JSONDecoder().raw_decode(text[i:])[0], out
            except ValueError:
                pass
    return None, (out + err)[-600:]


def orca_error(d: "dict | None") -> str:
    if d is None:
        return "unreadable"
    if d.get("ok") is False or d.get("error"):
        return str((d.get("error") or {}).get("code") or "error")
    return ""


# ---------------------------------------------------------------- pid files

def pid_alive(pidfile: Path) -> int:
    try:
        pid = int(pidfile.read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return 0
    cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
    return pid if "router.py" in cmd else 0


def launch(S: State, args: "list[str]", log: Path) -> int:
    with open(log, "ab") as f:
        p = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve())] + args,
            stdin=subprocess.DEVNULL, stdout=f, stderr=f, start_new_session=True, env=orca_env(S),
        )
    return p.pid


_stop = False
_term_ends_child = True


def _on_term(_sig: int, _frm: object) -> None:
    global _stop
    _stop = True
    if _child is not None and _term_ends_child:
        try:
            _child.terminate()
        except OSError:
            pass


# ---------------------------------------------------------------- mailbox daemon

def registry(S: State, dispatch: str, wait_s: float = 0.0) -> "dict | None":
    """Which chain and step started this worker. A worker can speak before its start receipt is recorded."""
    if not dispatch:
        return None
    path = S / "dispatches" / f"{safe(dispatch)}.json"
    deadline = time.time() + wait_s
    while True:
        reg = read_json(path)
        if reg is not None or time.time() >= deadline:
            return reg
        time.sleep(0.2)


def release(S: State, dispatch: str) -> str:
    d, raw = orca(S, ["orchestration", "worker-release", "--dispatch", dispatch], timeout=120)
    if orca_error(d):
        return f"release_unknown ({orca_error(d)})"
    r = d.get("result") or {}
    return f"{r.get('state', 'release_unknown')} ({r.get('processAction', '-')})"


def collect_later(S: State, dispatch: str) -> None:
    """A released worker is gone, so its session file is complete: its logs are gathered now, in a process of their
    own. Nothing here waits for it. A collector that fails journals "collector: <dispatch>: <why>" itself."""
    if not COLLECT or not dispatch:
        return
    try:
        (S / "logs").mkdir(exist_ok=True)
        with open(S / "logs" / "collector.log", "ab") as f:
            subprocess.Popen([sys.executable, str(HERE / "collector.py"), "collect", "--state", str(S.root), "--dispatch", dispatch,
                              "--wait-settled", str(COLLECT_WAIT_S)],
                             stdin=subprocess.DEVNULL, stdout=f, stderr=f, start_new_session=True, env=orca_env(S))
    except OSError as e:
        journal(S, "", "", "", dispatch, f"collector: {dispatch}: could not start: {e}")


def handle_message(S: State, m: dict) -> None:
    typ = m.get("type") or "status"
    try:
        payload = json.loads(m.get("payload") or "{}")
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    sender = m.get("from_handle") or ""
    dispatch = payload.get("dispatchId") or (sender.split(":", 1)[1] if sender.startswith("dispatch:") else "")
    task = payload.get("taskId") or ""
    created = m.get("created_at") or now()

    if typ == "heartbeat":
        if dispatch:
            live = S / "liveness" / safe(dispatch)
            old = live.read_text().split("\t")[0] if live.exists() else ""
            if created >= old:
                atomic_write(live, f"{created}\t{oneline(payload.get('phase'), 80)}\n")
        append(S / "heartbeats.log", f"{created} {task or '-'} {dispatch or '-'} {oneline(payload.get('phase'), 80)}")
        return

    ev = S / "events" / safe(dispatch or "_none") / f"{re.sub(r'[^0-9]', '', created)}-{typ}-{safe(m.get('id') or 'msg')}.json"
    if ev.exists():
        return  # a replayed delivery: already handled
    reg = registry(S, dispatch, wait_s=REGISTRY_WAIT_S)
    pr, step = (reg or {}).get("pr", ""), (reg or {}).get("step", "")
    record = {"message": m, "payload": payload, "received": now()}
    who = f"{pr or 'no chain'} · {step or '-'} · task {task or '-'} · dispatch {dispatch or '-'}"

    if typ == "worker_done":
        outcome = payload.get("outcome") or "unknown"
        if reg is not None:
            record["release"] = release(S, dispatch)
            reg.update(settled=now(), outcome=outcome)
            write_json(S / "dispatches" / f"{safe(dispatch)}.json", reg)
            if not record["release"].startswith(RELEASED_OK):
                wake(S, "release", pr, [f"WAKE release · {who}", f"worker-release answered: {record['release']}",
                                        "next: orca orchestration worker-list --terminal-state release_unknown --json, "
                                        "then follow its recovery receipt"], key=f"rel-{m.get('id')}")
        write_json(ev, record)
        journal(S, pr, step, task, dispatch, f"worker_done {outcome}; {record.get('release', 'not released: unknown worker')}; {m.get('subject')}")
        if reg is not None:
            collect_later(S, dispatch)
        if reg is None:
            wake(S, "unrouted", "", [f"WAKE unrouted · worker_done {outcome} from a worker the router did not start",
                                     f"subject: {m.get('subject')}", f"summary: {oneline(m.get('body'), 400)}",
                                     f"task {task or '-'} · dispatch {dispatch or '-'} · message {m.get('id')}",
                                     f"next: orca orchestration worker-release --dispatch {dispatch}"], key=f"un-{m.get('id')}")
        elif reg.get("adhoc"):
            wake(S, "done", pr, [f"WAKE done · ad hoc worker {reg.get('title')} · {outcome}",
                                 f"subject: {m.get('subject')}", f"summary: {oneline(m.get('body'), 400)}",
                                 f"report: {payload.get('reportPath') or 'none'}",
                                 f"task {task} · dispatch {dispatch} · {record['release']}"], key=f"adhoc-{m.get('id')}")
        return

    write_json(ev, record)
    if typ == "status":
        journal(S, pr, step, task, dispatch, f"status: {m.get('subject')}")
        return
    journal(S, pr, step, task, dispatch, f"{typ}: {m.get('subject')}")
    if typ == "question":
        opts = payload.get("options") or []
        lines = [f"WAKE question · {who} · message {m.get('id')}", f"question: {payload.get('question') or m.get('body')}"]
        if opts:
            lines.append("options: " + " | ".join(str(o) for o in opts))
        lines.append(f'next: router.py reply {m.get("id")} "<answer>"   (the worker is waiting; its chain keeps running)')
        wake(S, "question", pr, lines, key=f"q-{m.get('id')}")
    else:
        wake(S, typ, pr, [f"WAKE {typ} · {who} · message {m.get('id')}", f"subject: {m.get('subject')}",
                          f"body: {oneline(m.get('body'), 600)}",
                          f"full text: {ev}"], key=f"m-{m.get('id')}")


def silence_tick(S: State) -> None:
    """A live worker with no heartbeat and no message for SILENT_MIN rings once per silence."""
    for path in (S / "dispatches").glob("*.json"):
        reg = read_json(path) or {}
        if reg.get("settled"):
            continue
        live = S / "liveness" / path.stem
        last = max(epoch(reg.get("started", "")), epoch(live.read_text().split("\t")[0]) if live.exists() else 0.0)
        minutes = (time.time() - last) / 60
        if minutes < SILENT_MIN or reg.get("silent_rung_at", 0) >= last:
            continue
        reg["silent_rung_at"] = time.time()
        write_json(path, reg)
        wake(S, "silent", reg.get("pr", ""), [
            f"WAKE silent · {reg.get('pr') or 'no chain'} · {reg.get('step') or reg.get('title')} · dispatch {reg.get('dispatch')}",
            f"no heartbeat and no message for {int(minutes)} min (a heartbeat is due every 5)",
            "silence is not exit: never stop, retry or release on it",
            "next: router.py workers   (Orca's liveness verdict and its next action, one line per worker)"])


def cmd_mailbox(S: State, _a: argparse.Namespace) -> int:
    pidfile = S / "mailbox.pid"
    if pid_alive(pidfile):
        die("the mailbox daemon is already running")
    atomic_write(pidfile, f"{os.getpid()}\n")
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    run = (read_json(S / "run.json") or {}).get("run", "")
    print(f"{now()} mailbox daemon started for {run or 'the bound Run'} (pid {os.getpid()})", flush=True)
    page = serve_page(S)   # a thread: the loop below never waits for it
    ack, lost, unreadable = "", 0, 0
    while not _stop:
        flow_tick(S)          # a PR of the flow that became ready starts here, at the latest one long-poll later
        refresh_progress(S)   # at least once per long-poll: heartbeats and ages on the page stay current
        args = ["orchestration", "check"] + (["--run", run] if run else []) + (["--ack", ack] if ack else [])
        d, raw = orca(S, args + ["--wait", "--timeout-ms", str(WAIT_MS)], timeout=WAIT_MS / 1000 + 90)
        if _stop:
            break
        err = orca_error(d)
        if not err and (d.get("result") or {}).get("connectionLost") and not (d.get("result") or {}).get("messages"):
            err = "connection_lost"
        if err == "stale_delivery":  # the ack id no longer belongs to the mailbox: drop it, the batch replays if owed
            print(f"{now()} ack {ack} was stale, dropped", flush=True)
            ack = ""
            continue
        if err:
            if err == "unreadable":
                unreadable += 1
            else:
                lost += 1
            print(f"{now()} check answered {err}: {oneline(raw, 300)}", flush=True)
            if max(lost, unreadable) == 5:
                wake(S, "daemon", "", [f"WAKE daemon · the mailbox check failed 5 times in a row: {err}",
                                       f"last answer: {oneline(raw, 300)}",
                                       "contact loss is not a worker's death: no worker was stopped or retried",
                                       "next: orca status --json, then router.py init (restarts the daemon)"])
            time.sleep(min(20.0, POLL_S * 4))
            continue
        lost = unreadable = 0
        ack = ""
        result = d.get("result") or {}
        for m in result.get("messages") or []:
            try:
                handle_message(S, m)
            except Exception as e:  # one bad message must not stop the consumer
                print(f"{now()} message {m.get('id')} raised {e!r}", flush=True)
                wake(S, "daemon", "", [f"WAKE daemon · message {m.get('id')} ({m.get('type')}) could not be handled: {e!r}",
                                       "it was acknowledged; read it with: orca orchestration inbox --json"],
                     key=f"bad-{m.get('id')}")
        ack = result.get("deliveryId") or ""
        atomic_write(S / "mailbox.last", f"{now()}\n")
        silence_tick(S)
    refresh_progress(S, mailbox_stopped=True)   # the page's last word, before the pid file goes: `stop` waits for that
    if page is not None:
        page.server_close()
        (S / "page.json").unlink(missing_ok=True)
    pidfile.unlink(missing_ok=True)
    print(f"{now()} mailbox daemon stopped", flush=True)
    return 0


# ---------------------------------------------------------------- chains

def render(text: str, variables: "dict[str, str]", missing: "set[str] | None" = None, shell: bool = False) -> str:
    """Fill {NAME}. In a command (shell=True) each value is quoted, so a path with a space or a title with a quote
    stays one word and is never run: write {TITLE}, not "{TITLE}"."""
    def sub(m: "re.Match[str]") -> str:
        k = m.group(1)
        if k in variables:
            return shlex.quote(str(variables[k])) if shell else str(variables[k])
        if missing is None:
            raise RouterError(f"no value for {{{k}}}")
        missing.add(k)
        return m.group(0)
    return VAR_RE.sub(sub, text)


def step_var(step_id: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", step_id.upper())


def load_def(path: Path) -> dict:
    d = read_json(path)
    if not d or not isinstance(d.get("steps"), list):
        die(f"{path}: not a chain definition (a JSON object with a steps list)", 2)
    return d


def kit_of(def_path: Path, defn: dict) -> Path:
    """Where specs/ and checks/ are: the definition's own directory, or the "kit" it names relative to itself."""
    return (def_path.parent / str(defn.get("kit") or ".")).resolve()


def validate(defn: dict, kit: Path, variables: "dict[str, str]") -> "list[str]":
    """Every problem that would stop the chain later is found before the first worker starts."""
    problems: "list[str]" = []
    ids: "list[str]" = []
    known = set(variables)
    checkout: "bool | None" = None   # is WT a git checkout; asked once, the scheduler validates often
    for s in defn["steps"]:
        sid, typ = s.get("id", ""), s.get("type", "worker")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", sid or ""):
            problems.append(f"step id {sid!r}: use lower case letters, digits and _")
        if sid in ids:
            problems.append(f"step {sid}: the id is used twice")
        ids.append(sid)
        if typ not in ("worker", "script", "gate"):
            problems.append(f"step {sid}: type {typ!r} is not worker, script or gate")
            continue
        if s.get("group") and typ != "worker":
            problems.append(f"step {sid}: only worker steps can share a group")
        early = known | set(STEP_VARS_EARLY) | {"NOTE"}
        late = early | set(STEP_VARS_LATE)
        texts: "list[tuple[str, str, set[str]]]" = [("when", s.get("when", ""), early)]
        if typ == "worker":
            if not s.get("agent"):
                problems.append(f"step {sid}: a worker step needs an agent")
            spec = kit / "specs" / str(s.get("spec", ""))
            if not s.get("spec") or not spec.is_file():
                problems.append(f"step {sid}: spec file missing: {spec}")
            else:
                texts.append((f"spec {spec.name}", spec.read_text(), early))
            texts += [(k, s.get(k, ""), early) for k in ("worktree", "model", "effort")]
            if s.get("readonly") and "WT" not in variables:
                problems.append(f"step {sid}: readonly needs the WT variable (the PR's worktree)")
            elif s.get("readonly"):
                checkout = bool(git_head(variables["WT"])[0]) if checkout is None else checkout
                if not checkout:
                    problems.append(f"step {sid}: readonly needs WT to be a git checkout, and {variables['WT']} is not one")
            texts += [(f"check {i + 1}", c, late) for i, c in enumerate(s.get("checks") or [])]
        elif typ == "script":
            if not s.get("run"):
                problems.append(f"step {sid}: a script step needs run")
            texts.append(("run", s.get("run", ""), early))
        else:
            texts += [("title", s.get("title", ""), early)] + [(f"show {i + 1}", c, early) for i, c in enumerate(s.get("show") or [])]
        for where, text, allowed in texts:
            missing: "set[str]" = set()
            render(text, {k: "" for k in allowed}, missing)
            for k in sorted(missing):
                problems.append(f"step {sid}, {where}: no value for {{{k}}}")
        known |= set(s.get("exports") or []) | {f"HEAD_BEFORE_{step_var(sid)}", f"HEAD_AFTER_{step_var(sid)}"}
    return problems


def chain_dir(S: State, pr: str) -> Path:
    return S / "chains" / safe(pr)


def load_chain(S: State, pr: str) -> dict:
    st = read_json(chain_dir(S, pr) / "state.json")
    if st is None:
        die(f"no chain for {pr}: router.py chain {pr} --def <chain.json>")
    return st


_page_stale = False


def save_chain(S: State, st: dict) -> None:
    """The page is only marked stale here. Rewriting it takes long enough to matter between a saved state and the
    call that state announces ("starting", then worker-start): see flush_progress."""
    global _page_stale
    write_json(chain_dir(S, st["pr"]) / "state.json", st)
    _page_stale = True


def open_chains(S: State) -> "list[str]":
    """A PR holds its slot from its first step to its merge, also while it waits at a gate."""
    return sorted(st["pr"] for st in (read_json(p) or {} for p in (S / "chains").glob("*/state.json")) if st.get("status") not in (None, "done"))


def start_runner(S: State, pr: str) -> int:
    cdir = chain_dir(S, pr)
    if pid_alive(cdir / "runner.pid"):
        die(f"{pr}: its runner is already running")
    pid = launch(S, ["run", pr], cdir / "runner.log")
    atomic_write(cdir / "runner.pid", f"{pid}\n")
    return pid


def cmd_chain(S: State, a: argparse.Namespace) -> int:
    if pr_id_problem(a.pr):
        die(pr_id_problem(a.pr), 2)
    flow = load_flow(S)
    if flow_pr(flow, a.pr) is not None:
        die(f"the flow owns {a.pr}: router.py flow start {a.pr}")
    def_path = Path(a.definition).resolve()
    defn = load_def(def_path)
    variables = {str(k): str(v) for k, v in (defn.get("vars") or {}).items()}
    for kv in a.vars:
        if "=" not in kv:
            die(f"{kv!r}: variables are given as K=V", 2)
        k, v = kv.split("=", 1)
        variables[k] = v
    kit = kit_of(def_path, defn)
    if not kit.is_dir():
        die(f"{a.pr}: the definition's kit {defn.get('kit')!r} is not a directory: {kit}")
    variables.update(PR=a.pr, STATE=str(S.root), DEF_DIR=str(def_path.parent), KIT=str(kit), CHECKS=str(kit / "checks"))
    if os.environ.get("SCRATCH"):
        variables.setdefault("SCRATCH", os.environ["SCRATCH"])
    problems = validate(defn, kit, variables)
    for s in defn["steps"]:
        extra = f" ∥{s['group']}" if s.get("group") else ""
        what = {"worker": render(f"{s.get('agent')} {s.get('model', '')} {s.get('effort', '')}".strip(), variables, set()),
                "script": "script", "gate": "gate: the coordinator"}.get(s.get("type", "worker"), "?")
        print(f"  {s.get('id'):<16} {what}{extra}{'  when ' + s['when'] if s.get('when') else ''}")
    if problems:
        print(f"NOT OK {a.pr}: {len(problems)} problem(s), nothing created")
        for p in problems[:20]:
            print(f"  - {p}")
        return 1
    if a.dry_run:
        print(f"OK {a.pr}: {len(defn['steps'])} steps, dry run, nothing created")
        return 0
    cdir = chain_dir(S, a.pr)
    if (cdir / "state.json").exists():
        die(f"{a.pr}: a chain already exists ({cdir}); use resume or retry")
    cap = flow["slots"] if flow else MAX_CHAINS
    if len(open_chains(S)) >= cap:
        die(f"{len(open_chains(S))} chains are open ({', '.join(open_chains(S))}) and "
            + (f"the flow has {cap} slots" if flow else f"ROUTER_MAX_CHAINS is {MAX_CHAINS}") + f": {a.pr} was not started")
    if not pid_alive(S / "mailbox.pid"):
        die("the mailbox daemon is not running: router.py init")
    st = {"pr": a.pr, "def": str(def_path), "def_dir": str(def_path.parent), "kit_dir": str(kit), "created": now(), "status": "running",
          "vars": variables, "steps": [{"id": s["id"], "def": s, "status": "pending", "attempts": []} for s in defn["steps"]]}
    cdir.mkdir(parents=True, exist_ok=True)
    save_chain(S, st)
    journal(S, a.pr, "", "", "", f"chain created from {def_path.name}: {len(st['steps'])} steps")
    pid = start_runner(S, a.pr)
    print(f"OK {a.pr}: {len(st['steps'])} steps, runner pid {pid}")
    return 0


def git_head(wt: str) -> "tuple[str, str]":
    """(HEAD, fingerprint of the working tree's uncommitted changes); empty when wt is not a git checkout."""
    if not wt or not Path(wt).is_dir():
        return "", ""
    head = subprocess.run(["git", "-C", wt, "rev-parse", "HEAD"], capture_output=True, text=True)
    if head.returncode != 0:
        return "", ""
    dirty = subprocess.run(["git", "-C", wt, "status", "--porcelain"], capture_output=True, text=True).stdout
    return head.stdout.strip(), hashlib.sha1(dirty.encode()).hexdigest()[:12]


def run_line(cmd: str, cwd: str, timeout: float, env: "dict[str, str]") -> "tuple[int, str, list[str]]":
    """Run a check or script. Returns (exit code, its last line, every `VAR K=V` line it printed)."""
    try:
        p = subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True, text=True, errors="replace",
                           timeout=timeout, env=env)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {int(timeout)} s: {oneline(cmd, 120)}", []
    lines = [l for l in out.splitlines() if l.strip()]
    exports = [l[4:] for l in lines if l.startswith("VAR ") and "=" in l]
    plain = [l for l in lines if not l.startswith("VAR ")] or [l for l in err.splitlines() if l.strip()]
    return rc, oneline(plain[-1] if plain else f"exit {rc}, no output: {oneline(cmd, 120)}"), exports


def cwd_of(st: dict) -> str:
    """Where a step's commands run (run, when, checks, show): the kit, so `checks/x.sh` means the kit's checks. Without
    a kit that is the definition's own directory, as kit_of resolves it."""
    return st.get("kit_dir") or st["def_dir"]


def step_vars(st: dict, step: dict) -> "dict[str, str]":
    v = dict(st["vars"])
    att = step["attempts"][-1] if step["attempts"] else {}
    v.update(STEP=step["id"], ATTEMPT=str(max(1, len(step["attempts"]))), NOTE=step.get("note", ""),
             TASK=att.get("task", ""), DISPATCH=att.get("dispatch", ""), REPORT=att.get("report", ""),
             HEAD_BEFORE=att.get("head_before", ""), HEAD_AFTER=att.get("head_after", ""))
    return v


def condition(S: State, st: dict, step: dict) -> bool:
    when = step["def"].get("when")
    if not when:
        return True
    rc, _line, _ = run_line(render(when, step_vars(st, step), shell=True), cwd_of(st), 60, orca_env(S))
    return rc == 0


def start_worker(S: State, st: dict, step: dict) -> bool:
    d = dict(step["def"], **(step.get("override") or {}))
    n = len(step["attempts"]) + 1
    wt = st["vars"].get("WT", "")
    head, dirty = git_head(wt)
    # The step's range starts where its first attempt started. A retry keeps that start: otherwise a commit the
    # first attempt should not have made would fall outside the range its checks look at.
    base = step.setdefault("base", {"head": head, "dirty": dirty})
    attempt = {"n": n, "started": now(), "head_before": base["head"], "dirty_before": base["dirty"], "head_at_start": head}
    flow_stamp(st, step, attempt)
    step["attempts"].append(attempt)
    try:
        v = step_vars(st, step)
        spec = render((Path(st.get("kit_dir") or st["def_dir"]) / "specs" / d["spec"]).read_text(), v)
        worktree = render(d.get("worktree", "current"), v)
        model = render(d["model"], v) if d.get("model") else ""
        effort = render(d["effort"], v) if d.get("effort") else ""
    except (RouterError, OSError):
        step["attempts"].pop()
        raise
    step.pop("cause", None)
    if step.get("note"):
        spec += f"\n\nNote from the coordinator (attempt {n}): {step['note']}"
    title = f"{st['pr']} {step['id']}" + (f" (attempt {n})" if n > 1 else "")
    attempt["title"] = title
    # Written before the call: if this runner dies inside it, the next one must not start a second worker blindly.
    step["status"] = "starting"
    save_chain(S, st)
    args = ["orchestration", "worker-start", "--spec", spec, "--task-title", title,
            "--worktree", worktree, "--agent", d["agent"],
            "--timeout-ms", str(START_TIMEOUT_MS)]
    run = (read_json(S / "run.json") or {}).get("run")
    if run:
        args += ["--run", run]
    if model:
        args += ["--model", model]
        if effort:
            args += ["--effort", effort]
    r, raw = orca(S, args, timeout=START_TIMEOUT_MS / 1000 + 120)
    res = (r or {}).get("result") or {}
    if orca_error(r) or res.get("state") != "ready" or not res.get("dispatchId"):
        failed = (r or {}).get("error") or {}
        # No readable answer (a timeout, a lost runtime) is not a failed start: the worker may be running.
        known = r is not None and failed.get("code") != "runtime_unavailable"
        attempt.update(ended=now(), outcome="start_failed" if known else "start_unknown",
                       detail=oneline(failed.get("message") or res.get("failedStage") or res.get("stage") or raw, 300))
        step["status"] = attempt["outcome"]
        (chain_dir(S, st["pr"]) / f"start-{step['id']}-{n}.json").write_text(json.dumps(r, indent=1) if r else raw)
        journal(S, st["pr"], step["id"], "", "", f"worker-start failed: {attempt['detail']}")
        return False
    attempt.update(task=res.get("taskId", ""), dispatch=res["dispatchId"], agent=d["agent"], model=model, effort=effort,
                   worktree=worktree if worktree != "current" else current_worktree())   # for the collector
    launch_info = res.get("launch") or {}
    if launch_info.get("requested") != launch_info.get("effective"):
        attempt["launch_differs"] = f"asked {launch_info.get('requested')}, got {launch_info.get('effective')}"
        attempt["effective"] = launch_info.get("effective")
    write_json(S / "dispatches" / f"{safe(res['dispatchId'])}.json",
               {"dispatch": res["dispatchId"], "task": attempt["task"], "pr": st["pr"], "step": step["id"],
                "title": title, "started": attempt["started"]})
    step["status"] = "running"
    journal(S, st["pr"], step["id"], attempt["task"], attempt["dispatch"],
            render(f"started {d['agent']} {d.get('model', '')} {d.get('effort', '')}".rstrip(), v)
            + (f"; LAUNCH DIFFERS: {attempt['launch_differs']}" if attempt.get("launch_differs") else ""))
    return True


def worker_done(S: State, dispatch: str) -> "dict | None":
    files = sorted((S / "events" / safe(dispatch)).glob("*-worker_done-*.json"))
    return read_json(files[-1]) if files else None


def run_checks(S: State, st: dict, step: dict) -> "list[tuple[bool, str]]":
    d, att = step["def"], step["attempts"][-1]
    v = step_vars(st, step)
    results: "list[tuple[bool, str]]" = []
    if d.get("readonly"):
        head, dirty = git_head(st["vars"].get("WT", ""))
        if not head or not att.get("head_before"):
            results.append((False, f"NOT OK read-only could not be checked: {st['vars'].get('WT') or 'WT'} is not a git checkout"))
        elif head != att.get("head_before"):
            results.append((False, f"NOT OK read-only step moved HEAD: {att.get('head_before', '')[:9]} → {head[:9]}"))
        elif dirty != att.get("dirty_before"):
            results.append((False, "NOT OK read-only step changed uncommitted files in the PR's worktree"))
        else:
            results.append((True, f"OK read-only: HEAD {head[:9]} and the working tree are unchanged"))
    for c in d.get("checks") or []:
        rc, line, _ = run_line(render(c, v, shell=True), cwd_of(st), float(d.get("check_timeout", 300)), orca_env(S))
        ok = rc == 0 and not line.startswith("NOT OK")
        results.append((ok, line if line.startswith(("OK", "NOT OK")) else f"{'OK' if ok else 'NOT OK'} {line}"))
    return results


def pause(S: State, st: dict, kind: str, lines: "list[str]", gate: "dict | None" = None) -> None:
    """The ring first, the state second. A runner that dies in between rings twice; the other order would leave a
    paused chain nobody was told about, or a gate that `resume` passes before the coordinator has seen it."""
    wake(S, kind, st["pr"], lines)
    if gate is not None:
        gate["rung"] = True
    st["status"] = "paused"
    save_chain(S, st)


def mark_settled(S: State, dispatch: str, outcome: str) -> None:
    path = S / "dispatches" / f"{safe(dispatch)}.json"
    reg = read_json(path)
    if reg is not None and not reg.get("settled"):
        reg.update(settled=now(), outcome=outcome)
        write_json(path, reg)


def settle_step(S: State, st: dict, step: dict, ev: dict) -> None:
    """A worker_done arrived for this step's worker: record it, then let the checks decide."""
    att, payload, msg = step["attempts"][-1], ev.get("payload") or {}, ev.get("message") or {}
    if not ev.get("release"):  # it settled before the chain adopted it, so the daemon left its terminal alone
        ev["release"] = release(S, att["dispatch"])
        mark_settled(S, att["dispatch"], payload.get("outcome") or "unknown")   # or it would ring as silent later
        collect_later(S, att["dispatch"])
    head, _ = git_head(st["vars"].get("WT", ""))
    att.update(ended=now(), outcome=payload.get("outcome") or "unknown", report=payload.get("reportPath") or "",
               head_after=head, subject=oneline(msg.get("subject"), 200), summary=oneline(msg.get("body"), 400),
               release=ev.get("release", ""))
    sv = step_var(step["id"])
    st["vars"][f"HEAD_BEFORE_{sv}"], st["vars"][f"HEAD_AFTER_{sv}"] = att.get("head_before", ""), head
    if att["outcome"] != "succeeded":
        step["status"] = "failed"
        return
    results = run_checks(S, st, step)
    att["checks"] = [line for _ok, line in results]
    step["status"] = "done" if all(ok for ok, _ in results) else "check_failed"
    for _ok, line in results:
        journal(S, st["pr"], step["id"], att.get("task", ""), att.get("dispatch", ""), f"check: {line}")


def why_paused(st: dict, step: dict) -> "list[str]":
    pr, sid, att = st["pr"], step["id"], (step["attempts"][-1] if step["attempts"] else {})
    n = f"attempt {att.get('n', 1)}"
    ids = f"task {att.get('task') or '-'} · dispatch {att.get('dispatch') or '-'} · {att.get('release') or 'no terminal'}"
    if step["status"] == "failed":
        return [f"WAKE failed · {pr} · {sid} ({n})", f"worker_done {att.get('outcome')}: {att.get('subject')}",
                f"summary: {att.get('summary')}", f"report: {att.get('report') or 'none'}", ids,
                f'next: router.py retry {pr} --note "<what to change>"  |  router.py resume {pr} --accept "<why>"  |  router.py resume {pr} --from <step>']
    if step["status"] == "check_failed":
        return [f"WAKE check · {pr} · {sid} ({n}): the worker reported success, a check is not OK"] + list(att.get("checks") or []) + [
            ids, f'next: router.py retry {pr} --note "<the failing line>"  |  router.py resume {pr}  (re-runs the checks)  |  router.py resume {pr} --accept "<why>"']
    if step["status"] == "start_failed":
        return [f"WAKE start · {pr} · {sid} ({n}): the worker did not start", f"orca: {att.get('detail')}",
                f"receipt: {chain_dir_name(st)}/start-{sid}-{att.get('n', 1)}.json   (failedStage, residualResources, recovery)",
                "do not relaunch before reading the receipt: orca skills get orchestration --reference references/recovery-and-cleanup.md",
                f"next: router.py retry {pr} [--agent claude --model <id>]"]
    if step["status"] == "start_unknown":
        return [f"WAKE start · {pr} · {sid} ({n}): it is not known whether the worker started", f"why: {att.get('detail')}",
                f"a worker titled \"{att.get('title')}\" may be running. router.py workers shows it as a live worker with no chain",
                f"next: router.py resume {pr} --adopt <dispatch_id>   (it is running: the chain takes it over)",
                f"   or: router.py retry {pr}   (only after you have seen that none is running)"]
    if step["status"] == "script_failed":
        return [f"WAKE script · {pr} · {sid}: exit {att.get('rc')}", str(att.get("line")),
                f"next: router.py retry {pr}  |  router.py resume {pr} --accept \"<why>\""]
    return []


def chain_dir_name(st: dict) -> str:
    return f"chains/{safe(st['pr'])}"


def cmd_run(S: State, a: argparse.Namespace) -> int:
    """The runner. State on disk is the truth: a runner that dies is started again with resume."""
    global _term_ends_child
    st = load_chain(S, a.pr)
    cdir = chain_dir(S, a.pr)
    other = pid_alive(cdir / "runner.pid")
    if other and other != os.getpid():
        die(f"{a.pr}: runner {other} is already running")
    atomic_write(cdir / "runner.pid", f"{os.getpid()}\n")
    _term_ends_child = False
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    st["status"] = "running"
    for s in st["steps"]:
        if s["status"] == "starting":
            s["status"] = "start_unknown"
            s["attempts"][-1].update(outcome="start_unknown", detail="the runner ended while worker-start was running")
        elif s["status"] == "script":   # the last runner ended inside a script step: it runs again
            s["status"] = "pending"
            s["attempts"][-1].update(ended=now(), line="the runner ended while this script ran")
    save_chain(S, st)
    print(f"{now()} runner started for {a.pr}", flush=True)
    while not _stop:
        flush_progress(S)   # the last step's result is on the page before the next start, which can take minutes
        # A flow chain follows the flow's latest version here, between two steps. Under the flow lock, and the steps
        # it is about to start are claimed in the same breath: an apply cannot edit them while they start.
        with flow_lock(S) if st.get("flow") else contextlib.nullcontext():
            if st.get("flow"):
                try:
                    flow_reread(S, st)
                except RouterError as e:
                    journal(S, a.pr, "", "", "", f"paused: {e}")
                    pause(S, st, "runner", [f"WAKE runner · {a.pr}: {oneline(e, 300)}",
                                            "the chain paused rather than guess: nothing was started or passed",
                                            f"next: router.py flow show, a router.py flow apply that fixes the flow, then router.py resume {a.pr}"])
                    break
            steps = st["steps"]
            idx = next((i for i, s in enumerate(steps) if s["status"] not in DONE), None)
            if idx is None:
                st.update(status="done", ended=now())
                save_chain(S, st)
                journal(S, a.pr, "", "", "", "chain complete")
                break
            group = [steps[idx]]
            if steps[idx]["def"].get("group"):
                g = steps[idx]["def"]["group"]
                j = idx + 1
                while j < len(steps) and steps[j]["def"].get("group") == g:
                    group.append(steps[j])
                    j += 1
            if st.get("flow"):
                st["flow"]["claimed"] = [s["id"] for s in group if s["status"] == "pending"]
                save_chain(S, st)

        try:
            for step in group:
                if _stop:   # a stop between two starts of a group must not start the second
                    break
                if step["status"] != "pending":
                    continue
                typ = step["def"].get("type", "worker")
                if not condition(S, st, step):
                    step["status"] = "skipped"
                    head = git_head(st["vars"].get("WT", ""))[0]
                    st["vars"][f"HEAD_BEFORE_{step_var(step['id'])}"] = st["vars"][f"HEAD_AFTER_{step_var(step['id'])}"] = head
                    journal(S, a.pr, step["id"], "", "", f"skipped: {step['def'].get('when')}")
                elif typ == "gate":
                    step["status"] = "blocked"
                    step.pop("rung", None)
                elif typ == "script":
                    cmd = render(step["def"]["run"], step_vars(st, step), shell=True)
                    attempt = {"n": len(step["attempts"]) + 1, "started": now()}
                    flow_stamp(st, step, attempt)
                    step.pop("cause", None)
                    step["attempts"].append(attempt)
                    step["status"] = "script"   # saved, so status and progress show it: a CI wait can take an hour
                    save_chain(S, st)
                    flush_progress(S)
                    rc, line, exports = run_line(cmd, cwd_of(st), float(step["def"].get("timeout", 600)), orca_env(S))
                    for kv in exports:
                        k, val = kv.split("=", 1)
                        st["vars"][k.strip()] = val.strip()
                        if k.strip() in (st.get("flow") or {}).get("vars_keys", []):   # the run's value now, not the flow's
                            st["flow"]["vars_keys"].remove(k.strip())
                    step["attempts"][-1].update(ended=now(), rc=rc, line=line)
                    step["status"] = "done" if rc == 0 else "script_failed"
                    journal(S, a.pr, step["id"], "", "", f"script exit {rc}: {line}")
                else:
                    start_worker(S, st, step)
                save_chain(S, st)
            if st.get("flow"):   # every step of the group has left pending, or is left for the next boundary
                st["flow"]["claimed"] = []
                save_chain(S, st)

            # A start that failed or is unknown rings now; it does not wait for the group's other workers.
            while (not _stop and any(s["status"] == "running" for s in group)
                   and not any(s["status"] in ("start_failed", "start_unknown") for s in group)):
                flush_progress(S)
                for step in group:
                    if step["status"] == "running":
                        ev = worker_done(S, step["attempts"][-1]["dispatch"])
                        if ev is not None:
                            settle_step(S, st, step, ev)
                            save_chain(S, st)
                if any(s["status"] == "running" for s in group):
                    time.sleep(POLL_S)
            if _stop:
                break

            for step in group:
                if step["status"] == "recheck":
                    head = git_head(st["vars"].get("WT", ""))[0]   # a fix made since the ring is part of what is checked
                    step["attempts"][-1]["head_after"] = head
                    st["vars"][f"HEAD_AFTER_{step_var(step['id'])}"] = head
                    results = run_checks(S, st, step)
                    step["attempts"][-1]["checks"] = [line for _ok, line in results]
                    if st.get("flow"):   # a recheck is no new attempt: the attempt records it
                        step["attempts"][-1].setdefault("rechecks", []).append({"at": now(), "flow_version": st["flow"].get("version"), "cause": "recheck"})
                    step["status"] = "done" if all(ok for ok, _ in results) else "check_failed"
                    save_chain(S, st)

            stuck = [s for s in group if s["status"] in PAUSED]
            if stuck:
                gate = next((s for s in stuck if s["status"] == "blocked"), None)
                if gate is not None:
                    lines = [f"WAKE gate · {a.pr} · {gate['id']}: " + render(gate["def"].get("title", "the coordinator decides"), step_vars(st, gate))]
                    for c in gate["def"].get("show") or []:
                        lines.append(run_line(render(c, step_vars(st, gate), shell=True), cwd_of(st), 120, orca_env(S))[1])
                    lines.append(f"next: router.py resume {a.pr}   (after you have decided; to send it back: "
                                 f'router.py resume {a.pr} --from <step> --note "<what to change>")')
                    pause(S, st, "gate", lines, gate=gate)
                else:
                    lines = []
                    for s in stuck:
                        lines += why_paused(st, s)
                    still = [s["id"] for s in group if s["status"] == "running"]
                    if still:
                        lines.append(f"still running in the same group: {', '.join(still)} (it is picked up when the chain goes on)")
                    pause(S, st, stuck[0]["status"].replace("_failed", ""), lines)
                journal(S, a.pr, stuck[0]["id"], "", "", f"paused: {stuck[0]['status']}")
                break
        except Exception as e:  # a variable with no value, a file that cannot be read: ring, never die in a loop
            if st.get("flow"):
                st["flow"]["claimed"] = []
            cur = next((s for s in group if s["status"] not in DONE), group[0])
            text = oneline(str(e) or repr(e), 300)
            journal(S, a.pr, cur["id"], "", "", f"paused: the runner could not go on: {text}")
            pause(S, st, "runner", [
                f"WAKE runner · {a.pr} · {cur['id']}: the runner could not go on: {text}",
                "nothing was started or passed for this step",
                f"next: router.py resume {a.pr} --set NAME=<value>   (a variable that an accepted or skipped step never set)"
                f"  |  router.py resume {a.pr} --from <step>"])
            break
    if _stop and st["status"] == "running":
        st["status"] = "stopped"
        save_chain(S, st)
    if st.get("flow") and st["status"] == "done":
        flow_tick(S)    # its slot is free and PRs after it may be ready: they start now, not at the daemon's next tick
    flush_progress(S)   # before the pid file goes: `stop` waits for that, and nothing is written after it
    (cdir / "runner.pid").unlink(missing_ok=True)
    print(f"{now()} runner for {a.pr} ended: {st['status']}", flush=True)
    return 0


def require_idle(S: State, pr: str) -> dict:
    if pid_alive(chain_dir(S, pr) / "runner.pid"):
        die(f"{pr}: its runner is still running; nothing to resume (router.py status)")
    return load_chain(S, pr)


def refuse_if_live(S: State, pr: str, steps: "list[dict]") -> None:
    """Running a step again while its last worker is alive would put two workers on one branch."""
    for s in steps:
        att = s["attempts"][-1] if s["attempts"] else {}
        reg = registry(S, att.get("dispatch", ""))
        if s["status"] == "running" and reg is not None and not reg.get("settled"):
            die(f"{pr}: step {s['id']} still has a live worker ({att['dispatch']}). Let it finish (router.py resume {pr}), "
                f"or, when Orca shows it is gone (router.py workers): router.py fail {pr} --step {s['id']} --why \"<what Orca shows>\"")
        if s["status"] in ("starting", "start_unknown"):
            die(f"{pr}: it is not known whether step {s['id']}'s worker started, so running it again could put two workers "
                f"on one branch. Settle that first: router.py resume {pr} --adopt <dispatch_id>, or router.py retry {pr} "
                f"once router.py workers shows none")


def cmd_resume(S: State, a: argparse.Namespace) -> int:
    st = require_idle(S, a.pr)
    if st["status"] == "done":
        die(f"{a.pr}: the chain is complete")
    for kv in a.set or []:
        if "=" not in kv:
            die(f"{kv!r}: --set takes NAME=value", 2)
        k, v = kv.split("=", 1)
        if k in ((st.get("flow") or {}).get("vars_keys") or []):
            die(f"{a.pr}: the flow sets {k}, and its runner takes the flow's value at the next step: change it with router.py flow apply")
        st["vars"][k] = v
        journal(S, a.pr, "", "", "", f"coordinator: set {k}={oneline(v, 200)}")
    if a.from_step:
        ids = [s["id"] for s in st["steps"]]
        if a.from_step not in ids:
            die(f"{a.pr}: no step {a.from_step}; steps: {', '.join(ids)}", 2)
        refuse_if_live(S, a.pr, st["steps"][ids.index(a.from_step):])
        for n, s in enumerate(st["steps"][ids.index(a.from_step):]):
            if n > 0 or s["status"] in DONE:
                s.pop("base", None)   # a later step, or one that was complete, starts a new range
            s["status"] = "pending"
            s.pop("note", None)
            s.pop("rung", None)
            if st.get("flow") and s["attempts"]:
                s["cause"] = "resume_from"
        if a.note:
            st["steps"][ids.index(a.from_step)]["note"] = a.note
        journal(S, a.pr, a.from_step, "", "", f"coordinator: re-run from {a.from_step}" + (f"; note: {a.note}" if a.note else ""))
    elif a.adopt:
        unknown = [s for s in st["steps"] if s["status"] == "start_unknown"]
        if len(unknown) != 1:
            die(f"{a.pr}: --adopt needs exactly one step whose start is unknown; there are {len(unknown)}")
        s, att = unknown[0], unknown[0]["attempts"][-1]
        if registry(S, a.adopt) is not None:
            die(f"{a.pr}: {a.adopt} already belongs to {registry(S, a.adopt).get('pr')} {registry(S, a.adopt).get('step')}")
        att.update(dispatch=a.adopt, task=att.get("task", ""), outcome=None, ended=None)
        write_json(S / "dispatches" / f"{safe(a.adopt)}.json",
                   {"dispatch": a.adopt, "task": att.get("task", ""), "pr": a.pr, "step": s["id"],
                    "title": att.get("title", ""), "started": att.get("started", now())})
        s["status"] = "running"
        journal(S, a.pr, s["id"], "", a.adopt, "coordinator: adopted a worker whose start was unknown")
    else:
        for s in st["steps"]:
            if s["status"] == "blocked":
                if not s.get("rung"):   # its runner ended before the ring: passing it now would pass it unseen
                    print(f"{a.pr}: gate {s['id']} had not rung yet; it rings now, and nothing was passed")
                    continue
                s["status"] = "done"
                s.pop("rung", None)
                journal(S, a.pr, s["id"], "", "", "coordinator: gate passed" + (f"; {a.accept}" if a.accept else ""))
            elif s["status"] in PAUSED and a.accept:
                owed = [k for k in (s["def"].get("exports") or []) if not st["vars"].get(k)]
                if owed:
                    die(f"{a.pr}: step {s['id']} was to set {', '.join(owed)}, and a later step uses it. Give it: "
                        f"router.py resume {a.pr} --accept \"<why>\" " + " ".join(f"--set {k}=<value>" for k in owed))
                # Accepting takes the tree as it is now: that is where the next step's range starts.
                head, sv = git_head(st["vars"].get("WT", ""))[0], step_var(s["id"])
                st["vars"].setdefault(f"HEAD_BEFORE_{sv}", (s.get("base") or {}).get("head", head))
                st["vars"][f"HEAD_AFTER_{sv}"] = head
                journal(S, a.pr, s["id"], "", "", f"coordinator: accepted {s['status']}: {a.accept}")
                s["status"] = "done"
            elif s["status"] == "check_failed":
                s["status"] = "recheck"
            elif s["status"] in PAUSED:
                die(f"{a.pr}: step {s['id']} is {s['status']}: use retry, --accept \"<why>\" or --from <step>")
    save_chain(S, st)
    print(f"OK {a.pr}: resumed, runner pid {start_runner(S, a.pr)}")
    return 0


def cmd_retry(S: State, a: argparse.Namespace) -> int:
    st = require_idle(S, a.pr)
    again = [s for s in st["steps"] if s["status"] in ("failed", "check_failed", "start_failed", "start_unknown", "script_failed")]
    if not again:
        die(f"{a.pr}: no failed step to retry (router.py status)")
    for s in again:
        s["status"] = "pending"
        if a.note:
            s["note"] = a.note
        override = {k: v for k, v in (("agent", a.agent), ("model", a.model), ("effort", a.effort)) if v}
        if override:
            s["override"] = override
        journal(S, a.pr, s["id"], "", "", "coordinator: retry" + (f"; note: {a.note}" if a.note else "")
                + (f"; as {override}" if override else ""))
    save_chain(S, st)
    print(f"OK {a.pr}: retrying {', '.join(s['id'] for s in again)}, runner pid {start_runner(S, a.pr)}")
    return 0


def orca_workers(S: State) -> "tuple[list[dict], dict]":
    """Every worker row of the Run (Orca pages past 100), and the counts of the first page."""
    run = (read_json(S / "run.json") or {}).get("run")
    rows: "list[dict]" = []
    counts: dict = {}
    cursor = ""
    for _ in range(20):
        d, raw = orca(S, ["orchestration", "worker-list", "--include-remote"] + (["--run", run] if run else [])
                      + (["--cursor", cursor] if cursor else []), timeout=120)
        if orca_error(d):
            raise RouterError(f"worker-list answered {orca_error(d)}: {oneline(raw, 300)}")
        res = d.get("result") or {}
        rows += res.get("workers") or []
        counts = counts or (res.get("counts") or {})
        page = res.get("page") or {}
        cursor = page.get("nextCursor") or ""
        if not page.get("hasMore") or not cursor:
            break
    return rows, counts


def cmd_fail(S: State, a: argparse.Namespace) -> int:
    """A worker that ended without worker_done leaves its step running for ever. The coordinator writes it off
    here, and only when Orca agrees that it is gone: silence alone is never enough."""
    st = load_chain(S, a.pr)
    running = [s for s in st["steps"] if s["status"] == "running" and a.step in (None, s["id"])]
    if not running:
        die(f"{a.pr}: no running step" + (f" named {a.step}" if a.step else "") + "; there is nothing to write off (router.py status)")
    if len(running) > 1:
        die(f"{a.pr}: {len(running)} steps are running ({', '.join(s['id'] for s in running)}); name one with --step <id>")
    sid, dispatch = running[0]["id"], running[0]["attempts"][-1]["dispatch"]
    row = next((w for w in orca_workers(S)[0] if w.get("dispatchId") == dispatch), None)
    pj = (row or {}).get("projection") or {}
    state, verdict = (row or {}).get("workerState"), (pj.get("liveness") or {}).get("verdict")
    if row is None or not (state in ("failed", "stopped", "abandoned") or verdict == "exited"):
        nxt = " ".join((pj.get("nextAction") or {}).get("argv") or []) if isinstance((pj.get("nextAction") or {}).get("argv"), list) else ""
        die(f"NOT OK {a.pr}: Orca " + (f"shows {dispatch} as {state}, liveness {verdict}" if row else f"does not list {dispatch}")
            + "; nothing was changed. A step is written off only when Orca shows its worker stopped, failed, abandoned or exited"
            + (f". Orca's next action: {nxt}" if nxt else ""))
    pid = pid_alive(chain_dir(S, a.pr) / "runner.pid")
    if pid:
        os.kill(pid, signal.SIGTERM)
        for _ in range(80):
            if not pid_alive(chain_dir(S, a.pr) / "runner.pid"):
                break
            time.sleep(0.25)
        else:
            die(f"{a.pr}: its runner did not stop; nothing was changed")
    st = load_chain(S, a.pr)   # the runner may have settled the step while it stopped
    step = next(s for s in st["steps"] if s["id"] == sid)
    att = step["attempts"][-1]
    if step["status"] != "running" or att.get("dispatch") != dispatch:
        die(f"{a.pr}: step {sid} settled while this ran ({step['status']}); nothing was changed: router.py resume {a.pr}")
    rel = release(S, dispatch)
    mark_settled(S, dispatch, "gone")
    collect_later(S, dispatch)
    head = git_head(st["vars"].get("WT", ""))[0]
    att.update(ended=now(), outcome="gone", head_after=head, release=rel, report="",
               subject=f"no worker_done; Orca shows {state}, liveness {verdict}", summary=oneline(a.why, 400))
    st["vars"][f"HEAD_BEFORE_{step_var(sid)}"], st["vars"][f"HEAD_AFTER_{step_var(sid)}"] = att.get("head_before", ""), head
    step["status"] = "failed"
    st["status"] = "paused"
    save_chain(S, st)
    journal(S, a.pr, sid, att.get("task", ""), dispatch, f"coordinator: written off (Orca: {state}, {verdict}); {rel}; {a.why}")
    print(f"OK {a.pr}: {sid} is marked failed (Orca: {state}, liveness {verdict}); terminal {rel}\n"
          f"next: router.py retry {a.pr} --note \"<what the next worker should know>\"  |  router.py resume {a.pr} --from <step>")
    return 0


# ---------------------------------------------------------------- coordinator commands

def cmd_init(S: State, a: argparse.Namespace) -> int:
    if a.run:
        d, raw = orca(S, ["orchestration", "run-use", "--id", a.run], timeout=60, caller=True)
        if orca_error(d):
            die(f"run-use {a.run} answered {orca_error(d)}: {oneline(raw, 300)}")
    d, raw = orca(S, ["orchestration", "run-current"], timeout=60, caller=True)   # not as the terminal in an old run.json
    run = ((d or {}).get("result") or {}).get("run") or {}
    if orca_error(d) or not run.get("id"):
        die("this terminal is bound to no Run: orca orchestration run-create --objective \"…\", or router.py init --run <id>")
    write_json(S / "run.json", {"run": run["id"], "objective": run.get("objective", ""), "recorded": now(),
                               "terminal": os.environ.get("ORCA_TERMINAL_HANDLE") or run.get("coordinator_handle", ""),
                               "pane": os.environ.get("ORCA_PANE_KEY", "")})
    old = pid_alive(S / "mailbox.pid")
    if old:  # a new coordinator terminal took the Run over: the daemon must act for the new one
        os.kill(old, signal.SIGTERM)
        for _ in range(50):
            if not pid_alive(S / "mailbox.pid"):
                break
            time.sleep(0.2)
    pid = launch(S, ["mailbox"], S / "mailbox.log")
    time.sleep(1.0)
    if not pid_alive(S / "mailbox.pid"):
        die(f"the mailbox daemon did not stay up: tail {S / 'mailbox.log'}")
    print(f"OK run {run['id']} · mailbox daemon pid {pid} · state {S.root}")
    return 0


def cmd_reply(S: State, a: argparse.Namespace) -> int:
    d, raw = orca(S, ["orchestration", "reply", "--id", a.message, "--body", a.answer], timeout=60)
    if orca_error(d):
        die(f"reply answered {orca_error(d)}: {oneline(raw, 300)}")
    atomic_write(S / "replied" / safe(a.message), now() + "\n")
    journal(S, "", "", "", "", f"coordinator: replied to {a.message}: {oneline(a.answer, 200)}")
    refresh_progress(S)
    print(f"OK replied to {a.message}")
    return 0


def current_worktree() -> str:
    """`--worktree current` is the worktree of the terminal that asks: this command's checkout, as a path: selector."""
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return f"path:{top.stdout.strip()}" if top.returncode == 0 and top.stdout.strip() else "current"


def cmd_worker(S: State, a: argparse.Namespace) -> int:
    spec = Path(a.spec_file).read_text() if a.spec_file else a.spec
    if not spec:
        die("give --spec or --spec-file", 2)
    args = ["orchestration", "worker-start", "--spec", spec, "--task-title", a.label, "--worktree", a.worktree,
            "--agent", a.agent, "--timeout-ms", str(START_TIMEOUT_MS)]
    if a.model:
        args += ["--model", a.model] + (["--effort", a.effort] if a.effort else [])
    started = now()   # before the call, as a chain step's: the worker's session begins while worker-start blocks
    d, raw = orca(S, args, timeout=START_TIMEOUT_MS / 1000 + 120)
    res = (d or {}).get("result") or {}
    if orca_error(d) or res.get("state") != "ready":
        out = S / f"start-adhoc-{safe(a.label)}.json"
        out.write_text(json.dumps(d, indent=1) if d else raw)
        die(f"NOT OK {a.label}: the worker did not start; receipt: {out}")
    write_json(S / "dispatches" / f"{safe(res['dispatchId'])}.json",
               {"dispatch": res["dispatchId"], "task": res.get("taskId", ""), "pr": a.pr or "", "step": "",
                "title": a.label, "started": started, "adhoc": True,
                "who": " ".join(x for x in (a.agent, a.model, a.effort if a.model else "") if x),
                "agent": a.agent, "model": a.model or "", "effort": a.effort if a.model else "",
                "worktree": a.worktree if a.worktree != "current" else current_worktree()})
    journal(S, a.pr or "", "adhoc", res.get("taskId", ""), res["dispatchId"], f"started ad hoc worker {a.label} ({a.agent} {a.model or ''})")
    refresh_progress(S)
    print(f"OK {a.label}: task {res.get('taskId')} · dispatch {res['dispatchId']} · rings when done")
    return 0


def cmd_collect(S: State, a: argparse.Namespace) -> int:
    args = ["--all"] if a.all or not (a.pr or a.dispatch) else (["--pr", a.pr] if a.pr else ["--dispatch", a.dispatch])
    args += ["--force"] * a.force + ["--dry-run"] * a.dry_run
    return subprocess.run([sys.executable, str(HERE / "collector.py"), "collect", "--state", str(S.root)] + args, env=orca_env(S)).returncode


def collected_line(S: State) -> str:
    settled = {r.get("dispatch") for r in (read_json(p) or {} for p in (S / "dispatches").glob("*.json")) if r.get("settled")}
    rows = (S / "logs" / "index.jsonl").read_text().splitlines() if (S / "logs" / "index.jsonl").exists() else []
    done = set()
    for r in rows:
        with contextlib.suppress(ValueError):
            done.add(json.loads(r).get("dispatch"))
    line = f"logs    {len(done & settled)}/{len(settled)} settled dispatches collected"
    if len(done & settled) < len(settled):
        line += " · router.py collect --all" + ("" if COLLECT else " (ROUTER_COLLECT=0: nothing is collected by itself)")
    return line


def self_check(S: State) -> "list[str]":
    problems = []
    if (S / "run.json").exists() and not pid_alive(S / "mailbox.pid"):
        problems.append("the mailbox daemon is not running, so nothing takes the workers' messages: router.py init")
    for p in (S / "chains").glob("*/state.json"):
        # The pid first, the state second: a runner saves "paused", "stopped" or "done" before it exits, so a
        # runner that ends between the two reads is not reported as gone.
        alive = pid_alive(p.parent / "runner.pid")
        st = read_json(p) or {}
        if st.get("status") == "running" and not alive:
            problems.append(f"chain {st.get('pr')} is marked running but its runner is gone: router.py resume {st.get('pr')}")
    return problems


def cmd_wait(S: State, a: argparse.Namespace) -> int:
    deadline = time.time() + a.timeout_min * 60 if a.timeout_min else None
    while True:
        pending = sorted((S / "wake").glob("*.txt"))
        for i, f in enumerate(pending):
            seen = S / "wake" / "seen" / f.name
            try:
                os.replace(f, seen)   # claimed before it is printed: a second doorbell must not show the same ring
            except FileNotFoundError:
                continue
            print(seen.read_text().rstrip())
            refresh_progress(S)   # this ring has been picked up
            if len(pending) - i > 1:
                print(f"(+{len(pending) - i - 1} more waiting: run router.py wait again)")
            return 0
        problems = self_check(S)
        if problems:
            # A runner that is starting or pausing this instant looks dead: look twice, and let its ring win.
            time.sleep(1.0)
            if any((S / "wake").glob("*.txt")):
                continue
            problems = [x for x in self_check(S) if x in problems]
        if problems:
            print("ACT: " + " · ".join(problems))
            return 3
        if deadline and time.time() >= deadline:
            print("OK quiet: nothing needed the coordinator · " + status_line(S))
            return 0
        time.sleep(min(POLL_S, 2.0))


def age(ts: str) -> str:
    e = epoch(ts)
    if not e:
        return "never"
    m = int((time.time() - e) / 60)
    return f"{m} min ago" if m < 120 else f"{m // 60} h ago"


def status_line(S: State) -> str:
    run = (read_json(S / "run.json") or {}).get("run", "no run")
    mb = pid_alive(S / "mailbox.pid")
    last = (S / "mailbox.last").read_text().strip() if (S / "mailbox.last").exists() else ""
    waiting = len(list((S / "wake").glob("*.txt")))
    return f"{run} · mailbox {'alive pid ' + str(mb) if mb else 'NOT RUNNING'} · last delivery {age(last)} · {waiting} wake(s) waiting"


def cmd_status(S: State, _a: argparse.Namespace) -> int:
    print("router  " + status_line(S))
    try:
        flow = load_flow(S)
    except RouterError as e:
        print(f"flow    {e}")
        flow = None
    if flow:
        print(f"flow    v{flow['version']} · slots {len(open_chains(S))}/{flow['slots']} · start {flow['start']} · router.py flow show")
    url = page_url(S)
    print(f"page    {url} · router.py page --open" if url else "page    not served: the mailbox daemon serves it (router.py init)")
    for p in sorted((S / "chains").glob("*/state.json")):
        alive = pid_alive(p.parent / "runner.pid")
        st = read_json(p) or {}
        steps = st.get("steps") or []
        done = sum(1 for s in steps if s["status"] in DONE)
        cur = [s for s in steps if s["status"] not in DONE and s["status"] != "pending"]
        head = f"{st.get('pr'):<8}{st.get('status')}{'' if alive or st.get('status') != 'running' else ' (RUNNER GONE)'} · {done}/{len(steps)} steps"
        if not cur:
            print(head)
        for s in cur:
            att = s["attempts"][-1] if s["attempts"] else {}
            extra = ""
            if s["status"] == "running" and att.get("dispatch"):
                live = S / "liveness" / safe(att["dispatch"])
                hb = live.read_text().strip().split("\t") if live.exists() else ["", ""]
                extra = f" · {att['dispatch']} · started {age(att.get('started', ''))} · heartbeat {age(hb[0])}" + (f" ({hb[1]})" if len(hb) > 1 and hb[1] else "")
            print(f"{head} · {s['id']} {s['status']} (attempt {att.get('n', 1)}){extra}")
    for p in sorted((S / "dispatches").glob("*.json")):
        reg = read_json(p) or {}
        if reg.get("adhoc") and not reg.get("settled"):
            print(f"ad hoc  {reg.get('title')} · {reg.get('dispatch')} · started {age(reg.get('started', ''))}")
    for ev in sorted((S / "events").glob("*/*-question-*.json")):
        msg = (read_json(ev) or {}).get("message") or {}
        reg = registry(S, ev.parent.name) or {}
        if msg.get("id") and not (S / "replied" / safe(msg["id"])).exists() and not reg.get("settled"):
            print(f"question {msg['id']} · {reg.get('pr') or '-'} {reg.get('step') or reg.get('title') or '-'} · asked {age(msg.get('created_at', ''))} · "
                  f"NOT ANSWERED: router.py reply {msg['id']} \"<answer>\"   (the text: router.py last 5)")
    print(collected_line(S))
    jr = S / "journal.md"
    if jr.exists():
        for line in jr.read_text().splitlines()[-3:]:
            print("journal " + oneline(line, 200))
    return 0


def cmd_last(S: State, a: argparse.Namespace) -> int:
    """Print the newest rings again: a ring is shown once, and a session that died right after lost it."""
    seen = sorted((S / "wake" / "seen").glob("*.txt"))[-a.n:]
    for f in seen:
        print(f.read_text().rstrip())
        print("  (rang " + age(datetime.datetime.fromtimestamp(int(f.name.split("-")[0]) / 1e9, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")) + ")")
    if not seen:
        print("no ring so far")
    return 0


def cmd_workers(S: State, _a: argparse.Namespace) -> int:
    rows, counts = orca_workers(S)
    shown = 0
    for w in rows:
        pj = w.get("projection") or {}
        att = pj.get("attention") or {}
        reg = registry(S, w.get("dispatchId", "")) or {}
        # Orca keeps flagging a failed task after its terminal is released. The router already paused and rang for
        # it, and a retry is a new task, so a released worker the router settled owes nothing.
        if w.get("terminalState") == "released" and (reg.get("settled") or not att.get("requiresAction")):
            continue
        nxt = pj.get("nextAction") or {}
        argv = " ".join(nxt.get("argv") or []) if isinstance(nxt.get("argv"), list) else ""
        print(oneline(f"{w.get('dispatchId')} · {reg.get('pr') or '-'} {reg.get('step') or reg.get('title') or '-'} · "
                      f"{w.get('workerState')} · terminal {w.get('terminalState')} · liveness {(pj.get('liveness') or {}).get('verdict')} · "
                      f"attention {','.join(att.get('categories') or []) or 'none'}"
                      f"{' REQUIRES ACTION' if att.get('requiresAction') else ''} · next {nxt.get('kind')}{': ' + argv if argv else ''}", 300))
        shown += 1
    print(f"{shown} shown of {len(rows)} ({json.dumps(counts)}); released workers the router settled are left out")
    return 0


# ---------------------------------------------------------------- the owner's view

PAUSE_RINGS = ("gate", "failed", "check", "start", "start_unknown", "script", "runner")   # the rings a paused chain rang with


def ring_files(S: State, seen: bool) -> "list[tuple[str, str, str, Path]]":
    """(when it rang, the PR as the file names it, kind, file) for the rings still waiting, or for those shown."""
    out = []
    for f in sorted((S / "wake" / "seen" if seen else S / "wake").glob("*.txt")):
        ns, _, rest = f.stem.partition("-")
        pr, _, kind = rest.rpartition("-")
        try:
            rang = datetime.datetime.fromtimestamp(int(ns) / 1e9, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
        out.append((rang, pr, kind, f))
    return out


def heartbeat(S: State, dispatch: str) -> "tuple[str, str]":
    """(time, phase) of a worker's last heartbeat; empty before the first one."""
    live = S / "liveness" / safe(dispatch)
    if not dispatch or not live.exists():
        return "", ""
    hb = live.read_text().strip().split("\t")
    return hb[0], hb[1] if len(hb) > 1 else ""


def step_view(st: dict, s: dict) -> dict:
    d = dict(s["def"], **(s.get("override") or {}))
    att = s["attempts"][-1] if s["attempts"] else {}
    typ = d.get("type", "worker")
    who = ""
    if typ == "worker":   # the definition, with what a retry changed
        who = " ".join(x for x in (render(str(d.get(k) or ""), st["vars"], set()) for k in ("agent", "model", "effort")) if x)
    if s["status"] == "blocked":
        note = render(str(d.get("title") or ""), st["vars"], set())
    elif s["status"] == "check_failed":
        note = next((c for c in att.get("checks") or [] if c.startswith("NOT OK")), "")
    elif s["status"] == "failed" and att.get("outcome") == "gone":   # written off with `fail`: there was no worker_done
        note = f"written off: {att.get('subject') or ''}"
    elif s["status"] == "failed":
        note = f"worker_done {att.get('outcome')}: {att.get('subject') or ''}"
    elif s["status"] in ("start_failed", "start_unknown"):
        note = att.get("detail") or ""
    else:
        note = att.get("line") or ""   # a script's last line
    return {"id": s["id"], "type": typ, "status": s["status"], "group": d.get("group") or "", "n": max(1, len(s["attempts"])),
            "started": att.get("started") or "", "ended": att.get("ended") or "", "who": who, "note": oneline(note, 300),
            "title": att.get("title") or "", "dispatch": att.get("dispatch") or ""}


def pr_row(S: State, item: dict, st: "dict | None") -> dict:
    row = {"id": str(item.get("id") or ""), "title": str(item.get("title") or ""), "part": str(item.get("part") or ""),
           "state": "not started", "created": "", "ended": "", "done": 0, "total": 0, "at": [], "why": "", "since": "",
           "picked_up": True, "url": "", "steps": [], "waits": ""}
    if st is None:
        return row
    steps = [step_view(st, s) for s in st.get("steps") or []]
    stuck = [s for s in steps if s["status"] in PAUSED]
    state = st.get("status") or "unknown"
    if state == "running" and not pid_alive(chain_dir(S, st["pr"]) / "runner.pid"):
        state = "runner gone"
    elif state == "paused" and stuck and all(s["status"] == "blocked" for s in stuck):
        state = "at a gate"
    at = [s["id"] for s in steps if s["status"] not in DONE and s["status"] != "pending"]
    if not at and state != "done":
        at = [s["id"] for s in steps if s["status"] == "pending"][:1]
    why = "; ".join(f"{s['id']}: {s['note'] or s['status']}" for s in stuck)
    if state == "paused" and not stuck:
        why = "the runner could not go on: router.py last"
    row.update(id=st["pr"], title=row["title"] or str(st["vars"].get("TITLE") or ""), state=state, created=st.get("created") or "",
               ended=st.get("ended") or "", done=sum(1 for s in steps if s["status"] in DONE), total=len(steps), at=at,
               why=why, url=str(st["vars"].get("PR_URL") or ""), steps=steps)
    return row


def progress_data(S: State, mailbox_stopped: bool = False) -> dict:
    """What the owner's view shows. It reads the state directory and asks Orca nothing, so a daemon can afford it
    at every change."""
    run = read_json(S / "run.json") or {}
    try:
        flow = load_flow(S)
    except RouterError:
        flow = None   # the daemon rings for it; the view falls back to the plan
    plan = flow or read_json(S / "plan.json") or {}   # with a flow, the flow is the plan
    planned = [p for p in plan.get("prs") or [] if isinstance(p, dict)]
    chains = {}
    for p in (S / "chains").glob("*/state.json"):
        st = read_json(p)
        if st and st.get("pr"):
            chains[st["pr"].lower()] = st
    rows = [pr_row(S, item, chains.pop(str(item.get("id") or "").lower(), None)) for item in planned]
    if flow:
        for r, p in zip(rows, planned):
            if r["state"] == "not started":
                r["waits"] = flow_waits(S, flow, p) or "ready"
    for st in sorted(chains.values(), key=lambda c: c.get("created") or ""):
        rows.append(pr_row(S, {"id": st["pr"], "part": ("not in the flow" if flow else "not in the plan") if planned else ""}, st))

    rang: "dict[str, tuple[str, bool]]" = {}   # the newest ring a paused chain rang with, and whether it was picked up
    for seen in (True, False):
        for when, pr, kind, _f in ring_files(S, seen):
            if kind in PAUSE_RINGS and when >= rang.get(pr, ("", True))[0]:
                rang[pr] = (when, seen)
    workers = []
    for r in rows:
        if r["state"] in ("at a gate", "paused"):
            r["since"], r["picked_up"] = rang.get(safe(r["id"]), ("", True))
        for s in r["steps"]:
            if s["status"] in ("running", "starting", "script"):
                beat, phase = heartbeat(S, s["dispatch"])
                workers.append({"kind": "script" if s["status"] == "script" else "worker", "pr": r["id"], "step": s["id"],
                                "title": s["title"] or f"{r['id']} {s['id']}", "who": s["who"], "n": s["n"],
                                "started": s["started"], "dispatch": s["dispatch"], "heartbeat": beat, "phase": phase})
    for p in sorted((S / "dispatches").glob("*.json")):
        reg = read_json(p) or {}
        if reg.get("adhoc") and not reg.get("settled"):
            beat, phase = heartbeat(S, reg.get("dispatch") or "")
            workers.append({"kind": "ad hoc", "pr": reg.get("pr") or "", "step": "", "title": reg.get("title") or "",
                            "who": reg.get("who") or "", "n": 1, "started": reg.get("started") or "",
                            "dispatch": reg.get("dispatch") or "", "heartbeat": beat, "phase": phase})
    questions = []
    for ev in sorted((S / "events").glob("*/*-question-*.json")):
        rec = read_json(ev) or {}
        msg, reg = rec.get("message") or {}, registry(S, ev.parent.name) or {}
        if msg.get("id") and not (S / "replied" / safe(msg["id"])).exists() and not reg.get("settled"):
            questions.append({"id": msg["id"], "pr": reg.get("pr") or "", "step": reg.get("step") or reg.get("title") or "",
                              "asked": msg.get("created_at") or "",
                              "text": oneline((rec.get("payload") or {}).get("question") or msg.get("body"), 400)})
    events = []
    if (S / "journal.md").exists():
        for line in (S / "journal.md").read_text().splitlines()[-12:]:
            f = line.split(" | ", 5)
            if len(f) == 6:
                events.append({"t": f[0], "pr": f[1], "step": f[2], "text": f[5]})
    last = (S / "mailbox.last").read_text().strip() if (S / "mailbox.last").exists() else ""
    return {"written": now(), "run": run.get("run") or "", "objective": run.get("objective") or "",
            "title": str(plan.get("title") or ""), "has_plan": bool(planned), "state_dir": str(S.root),
            "flow": {"version": flow["version"], "slots": flow.get("slots"), "start": flow.get("start")} if flow else None,
            "page": str(S / "progress.html"), "refresh_s": WAIT_MS / 1000, "mailbox": {"alive": 0 if mailbox_stopped else pid_alive(S / "mailbox.pid"), "last": last},
            "rows": rows, "workers": workers, "questions": questions, "events": events,
            "rings": [{"rang": when, "pr": pr, "kind": kind, "line": oneline(f.read_text().splitlines()[0] if f.exists() and f.read_text() else "", 200)}
                      for when, pr, kind, f in ring_files(S, False)]}


def refresh_progress(S: State, mailbox_stopped: bool = False) -> None:
    """Rewrite progress.html from the state on disk. It never raises: the view must not stop a daemon."""
    global _page_stale
    _page_stale = False
    try:
        import progress
        page = S / "progress.html"
        tmp = page.with_name(f".{page.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(progress.html_page(progress_data(S, mailbox_stopped)))   # no mkdir: a removed state directory stays removed
        os.replace(tmp, page)
    except Exception as e:
        print(f"{now()} progress.html was not rewritten: {e!r}", file=sys.stderr, flush=True)


def flush_progress(S: State) -> None:
    """Rewrite the page if a chain was saved since the last rewrite. Called where the caller is about to wait or to
    exit, never between a saved state and the action it announces."""
    if _page_stale:
        refresh_progress(S)


def cmd_progress(S: State, _a: argparse.Namespace) -> int:
    try:
        import progress
        d = progress_data(S)
        atomic_write(S / "progress.html", progress.html_page(d))
        print(progress.text(d))
    except Exception as e:
        die(f"the view could not be rendered: {e!r}")
    return 0


def cmd_plan(S: State, a: argparse.Namespace) -> int:
    if load_flow(S) is not None:
        die("the flow is the plan: router.py flow apply <file> changes it, router.py flow show shows it")
    plan = read_json(Path(a.file)) or {}
    prs = plan.get("prs")
    if not isinstance(prs, list) or not prs or not all(isinstance(p, dict) and p.get("id") for p in prs):
        die(f"{a.file}: not a plan (a JSON object with prs, a list of objects that each have an id)", 2)
    ids = [str(p["id"]).lower() for p in prs]
    twice = sorted({i for i in ids if ids.count(i) > 1})
    if twice:
        die(f"{a.file}: an id is in the plan twice: {', '.join(twice)}", 2)
    write_json(S / "plan.json", plan)
    refresh_progress(S)
    parts = len({str(p.get("part") or "") for p in prs})
    print(f"OK plan: {len(prs)} inner PRs in {parts} part(s) · the id is the <pr> of router.py chain · page {S / 'progress.html'}")
    return 0


def cmd_stop(S: State, a: argparse.Namespace) -> int:
    targets = []
    if a.all or not a.pr:
        targets.append(("mailbox", S / "mailbox.pid"))
    for p in (S / "chains").glob("*/runner.pid"):
        if a.all or p.parent.name == safe(a.pr or ""):
            targets.append((p.parent.name, p))
    stopped = []
    for name, pidfile in targets:
        pid = pid_alive(pidfile)
        if pid:
            os.kill(pid, signal.SIGTERM)
            stopped.append(name)
    for _ in range(60):
        if not any(pid_alive(p) for _n, p in targets):
            break
        time.sleep(0.25)
    left = [n for n, p in targets if pid_alive(p)]
    print(f"OK stopped: {', '.join(stopped) or 'nothing was running'}" + (f" · STILL RUNNING: {', '.join(left)}" if left else "")
          + " · workers are untouched; a stopped chain goes on with router.py resume <pr>")
    return 1 if left else 0


# ---------------------------------------------------------------- the flow

ROUTER_VARS = ("PR", "STATE", "DEF_DIR", "KIT", "CHECKS", "BASE_BRANCH")   # the router sets these for a flow PR
FLOW_OWN = ("version", "history", "dir", "resolved", "removed")             # written by the router in its copy only
PR_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")                        # what safe() leaves as it is
STARTS = ("auto", "manual")


def pr_id_problem(pr: str) -> str:
    if PR_ID_RE.fullmatch(pr or ""):
        return ""
    return f"{pr!r}: a PR id is letters, digits, '.', '_' and '-', and starts with a letter or digit"


_lock = threading.local()   # per thread: the daemon's page server applies from its own thread


@contextlib.contextmanager
def flow_lock(S: State):
    """flow apply, a scheduler pass and a runner's re-read take turns: none sees another half done, and two passes
    that race (the daemon's tick, a runner that just finished, an apply from the page) cannot start one PR twice or
    overfill the slots. Each thread opens the file itself, so two threads of one daemon also take turns."""
    if not getattr(_lock, "depth", 0):
        _lock.fd = open(S / "flow.lock", "a")
        fcntl.flock(_lock.fd, fcntl.LOCK_EX)
    _lock.depth = getattr(_lock, "depth", 0) + 1
    try:
        yield
    finally:
        _lock.depth -= 1
        if not _lock.depth:
            _lock.fd.close()   # closing it releases the lock
            _lock.fd = None


def load_flow(S: State) -> "dict | None":
    """The router's copy of the flow; None before the first apply. Raises RouterError when it cannot be read."""
    path = S / "flow.json"
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise RouterError(f"the flow could not be read: {e}")
    try:
        f = json.loads(text)
    except ValueError as e:
        raise RouterError(f"the flow could not be read: {path.name}: {e}")
    if not isinstance(f, dict) or not isinstance(f.get("prs"), list) or not isinstance(f.get("resolved"), dict) \
            or not isinstance(f.get("version"), int):
        raise RouterError(f"the flow could not be read: {path.name} is not the router's copy of a flow")
    return f


def flow_pr(f: "dict | None", pr: str) -> "dict | None":
    return next((p for p in (f or {}).get("prs") or [] if isinstance(p, dict) and str(p.get("id", "")).lower() == pr.lower()), None)


def def_sha(d: dict) -> str:
    return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12]


def fill_values(layers: "dict[str, str]", own: "dict[str, str]", declared: "set[str]", where: "dict[str, str]") -> "tuple[dict[str, str], set[str], list[str]]":
    """Fill the {NAME}s inside variable values ("WT": "{SCRATCH}/wt/{PR}"). Returns (values, the names a value waits
    for: declared by the template and given by nobody, a problem line for a name nobody declares or a loop). A line
    names the layer the value came from: where[NAME] ("profile bell", "template one", "A1"), the flow's own vars else."""
    raw = dict(layers, **own)
    done: "dict[str, str]" = dict(own)
    waits: "set[str]" = set()
    problems: "list[str]" = []

    def label(k: str) -> str:
        return f"{where[k]}: vars.{k}" if where.get(k) else f"vars.{k}"

    def fill(k: str, seen: "list[str]") -> str:
        if k in done:
            return done[k]
        if k in seen:
            problems.append(f"{label(seen[0])}: its value names itself: {' -> '.join(seen + [k])}")
            return raw[k]

        def sub(m: "re.Match[str]") -> str:
            n = m.group(1)
            if n in raw:
                return fill(n, seen + [k])
            if n in declared:
                waits.add(n)
            else:
                problems.append(f"{label(k)}: {{{n}}} is not a variable")
            return m.group(0)
        done[k] = VAR_RE.sub(sub, raw[k])
        return done[k]
    for k in layers:
        fill(k, [])
    return done, waits, problems


def flow_effective(S: State, f: dict, p: dict) -> dict:
    """One PR as the flow defines it: the definition (the template's, with the PR's own steps when it has them), the
    kit, and the variables. Variables, lowest first: SCRATCH from the environment of the apply, the profile's, the
    template's, the flow's, the PR's, then the router's own. Everything comes from the copy: no file is read."""
    t = f["resolved"]["templates"][p["template"]]
    tdef, kit = t["def"], Path(t["kit"])
    layers: "dict[str, str]" = {}
    where: "dict[str, str]" = {}   # the layer each value comes from, for a problem line; "" is the flow's own vars
    profile = f["resolved"].get("profile") or {}
    for name, layer in (("environment", f["resolved"].get("env")), (f"profile {profile.get('name') or ''}".strip(), profile.get("vars")),
                        (f"template {p['template']}", tdef.get("vars")), ("", f.get("vars")), (p["id"], p.get("vars"))):
        for k, v in (layer or {}).items():
            if k not in ROUTER_VARS:
                layers[str(k)], where[str(k)] = str(v), name
    own = {"PR": p["id"], "STATE": str(S.root), "DEF_DIR": str(Path(t["path"]).parent), "KIT": str(kit), "CHECKS": str(kit / "checks")}
    if p.get("base"):
        own["BASE_BRANCH"] = str(p["base"])
    values, waits, problems = fill_values(layers, own, set(tdef.get("variables") or {}), where)
    override = isinstance(p.get("steps"), list)
    return {"def": dict(tdef, steps=p["steps"] if override else tdef["steps"]), "kit": str(kit), "def_path": t["path"],
            "def_dir": str(Path(t["path"]).parent), "override": override, "vars": values, "waits": waits, "problems": problems}


def def_problems(eff: dict, skip: "set[str]" = frozenset()) -> "tuple[set[str], list[str], list[str]]":
    """validate() on a flow PR, sorted: (variables nobody gives, other things it waits for, problems that refuse it).
    Problems of the steps in skip (settled ones) are left out: they ran, and nothing will render them again."""
    missing, other, hard = set(eff["waits"]), [], []
    for prob in validate(eff["def"], Path(eff["kit"]), eff["vars"]):
        if any(prob.startswith((f"step {sid}:", f"step {sid},")) for sid in skip):
            continue
        m = re.search(r"no value for \{([A-Z][A-Z0-9_]*)\}$", prob)
        if m:
            missing.add(m.group(1))
        elif "readonly needs the WT variable" in prob:
            missing.add("WT")
        elif "readonly needs WT to be a git checkout" in prob:
            if "WT to be a git checkout" not in other:
                other.append("WT to be a git checkout")
        else:
            hard.append(prob)
    return missing, other, hard


def flow_waits(S: State, f: dict, p: dict) -> str:
    """What a PR that has no chain waits for, in one phrase; "" when it can start (a free slot aside)."""
    try:
        missing, other, hard = def_problems(flow_effective(S, f, p))
    except Exception as e:   # a copy the router cannot read: the scheduler rings for it
        return f"cannot start: {e!r}"
    ids = {str(q.get("id", "")).lower(): q.get("id") for q in f["prs"]}
    after = [ids.get(a.lower(), a) for a in p.get("after") or []]
    parts = []
    if missing or other:
        parts.append("waiting for: " + ", ".join(sorted(missing) + other))
    pending = [a for a in after if (read_json(chain_dir(S, a) / "state.json") or {}).get("status") != "done"]
    if pending:
        parts.append("after " + ", ".join(pending))
    if hard:
        parts.append("cannot start: " + hard[0])
    return " · ".join(parts)


def vars_problems(where: str, obj: object) -> "list[str]":
    if obj is None:
        return []
    if not isinstance(obj, dict):
        return [f"{where}: an object of NAME: value"]
    out = []
    for k, v in obj.items():
        if k == "BASE_BRANCH":
            out.append(f"{where}.BASE_BRANCH: the router sets it from the PR's base")
        elif k in ROUTER_VARS:
            out.append(f"{where}.{k}: the router sets it")
        elif isinstance(v, bool) or not isinstance(v, (str, int, float)):
            out.append(f"{where}.{k}: the value must be a string or a number")
    return out


def flow_build(S: State, raw: object, base_dir: Path) -> "tuple[dict, list[str]]":
    """The router's copy of a flow given to apply (without version and history), and every problem that refuses it,
    one line each. Templates and the profile are read here, once: the copy holds them, so a version never changes."""
    if not isinstance(raw, dict):
        return {}, ["not a flow: a JSON object with templates and a prs list"]
    f = {k: v for k, v in raw.items() if k not in FLOW_OWN}
    f.setdefault("slots", MAX_CHAINS)
    f.setdefault("start", "auto")
    f["dir"] = str(base_dir)
    problems: "list[str]" = []
    if isinstance(f["slots"], bool) or not isinstance(f["slots"], int) or f["slots"] < 1:
        problems.append(f"slots: {f['slots']!r} is not a positive integer")
    if f["start"] not in STARTS:
        problems.append(f"start: {f['start']!r} is not auto or manual")
    resolved: dict = {"templates": {}, "profile": None, "env": {"SCRATCH": os.environ["SCRATCH"]} if os.environ.get("SCRATCH") else {}}
    f["resolved"] = resolved
    templates = f.get("templates")
    if not isinstance(templates, dict) or not templates:
        problems.append("templates: an object of name -> chain definition file")
        templates = {}
    for name, rel in templates.items():
        path = (base_dir / str(rel)).resolve()
        if not isinstance(rel, str) or not path.is_file():
            problems.append(f"templates.{name}: not a file: {path}")
            continue
        d = read_json(path)
        if not isinstance(d, dict) or not isinstance(d.get("steps"), list) or not all(isinstance(s, dict) for s in d["steps"]):
            problems.append(f"templates.{name}: {path} is not a chain definition (a JSON object with a steps list)")
            continue
        kit = kit_of(path, d)
        if not kit.is_dir():
            problems.append(f"templates.{name}: its kit {d.get('kit')!r} is not a directory: {kit}")
            continue
        problems += vars_problems(f"templates.{name}: vars", d.get("vars"))
        resolved["templates"][name] = {"path": str(path), "kit": str(kit), "def": d}
    if f.get("profile"):
        path = (base_dir / str(f["profile"])).resolve()
        d = read_json(path) if path.is_file() else None
        if not path.is_file():
            problems.append(f"profile: not a file: {path}")
        elif not isinstance(d, dict) or not isinstance(d.get("vars", {}), dict):
            problems.append(f"profile: {path} is not a profile (a JSON object with a vars object)")
        else:
            problems += vars_problems("profile: vars", d.get("vars"))
            resolved["profile"] = {"path": str(path), "name": str(d.get("name") or ""), "vars": d.get("vars") or {}}
    problems += vars_problems("vars", f.get("vars"))

    prs = f.get("prs")
    if not isinstance(prs, list) or not prs or not all(isinstance(p, dict) for p in prs):
        return f, problems + ["prs: a list of objects, each with an id"]
    ids: "dict[str, list[str]]" = {}
    for i, p in enumerate(prs):
        pid = p.get("id")
        if not isinstance(pid, str) or not pid:
            problems.append(f"prs[{i}]: no id")
            continue
        if pr_id_problem(pid):
            problems.append(pr_id_problem(pid))
        ids.setdefault(pid.lower(), []).append(pid)
    for names in ids.values():
        if len(names) > 1:
            problems.append(f"{names[0]}: the id is in the flow {len(names)} times ({', '.join(names)}); upper and lower case are the same")
    graph: "dict[str, list[str]]" = {}
    shared: "dict[str, tuple[int, list[str]]]" = {}   # a line of a shared layer: its place in problems, the PRs it fails for
    rendered: "list[str]" = []
    for p in prs:
        pid = p.get("id")
        if not isinstance(pid, str) or not pid:
            continue
        after = p.get("after", [])
        if not isinstance(after, list) or not all(isinstance(x, str) for x in after):
            problems.append(f"{pid}: after must be a list of PR ids")
            after = []
        for x in after:
            if x.lower() not in ids:
                problems.append(f"{pid}: after names {x}, which is not in the flow")
        graph[pid.lower()] = [x.lower() for x in after if x.lower() in ids]
        base = p.get("base")
        if base is not None and not isinstance(base, str):
            problems.append(f"{pid}: base must be a branch name")
        elif isinstance(base, str) and base.strip().lower() in ("main", "master"):
            problems.append(f"{pid}: base {base}: inner PRs go into the integration branch, never main or master")
        problems += vars_problems(f"{pid}: vars", p.get("vars"))
        if "steps" in p and (not isinstance(p["steps"], list) or not all(isinstance(s, dict) for s in p["steps"])):
            problems.append(f"{pid}: steps must be a list of steps (objects)")
            continue
        t = p.get("template")
        if t is None:
            problems.append(f"{pid}: no template; the flow's templates: {', '.join(sorted(templates)) or 'none'}")
            continue
        if t not in templates:
            problems.append(f"{pid}: template {t!r} is not one of the flow's templates ({', '.join(sorted(templates)) or 'none'})")
            continue
        if t not in resolved["templates"] or problems and any(x.startswith(f"{pid}: vars") for x in problems):
            continue
        eff = flow_effective(S, f, p)
        rendered.append(pid)
        for line in eff["problems"]:   # a flow, template or profile value is named once, not once per PR
            if line.startswith(f"{pid}: "):
                problems.append(line)
                continue
            if line not in shared:
                shared[line] = (len(problems), [])
                problems.append(line)
            shared[line][1].append(pid)
        st = read_json(chain_dir(S, pid) / "state.json") or {}
        settled = {s["id"] for s in st.get("steps") or [] if s.get("status") != "pending"}
        problems += [f"{pid}: {x}" for x in def_problems(eff, settled)[2]]
    for line, (i, pids) in shared.items():   # it fails only where a PR's own vars do not give the name: say which
        if len(pids) < len(rendered):
            problems[i] = f"{line} for {', '.join(pids)}"
    problems += cycles(graph, {k: v[0] for k, v in ids.items()})
    return f, problems


def cycles(graph: "dict[str, list[str]]", name: "dict[str, str]") -> "list[str]":
    """One line per cycle in "after", as the ids that make it."""
    out, state = [], {}

    def visit(n: str, path: "list[str]") -> None:
        state[n] = 1
        for m in graph.get(n, []):
            if state.get(m) == 1:
                loop = path[path.index(m):] + [m]
                out.append("after: a cycle: " + " -> ".join(name[x] for x in loop))
            elif not state.get(m):
                visit(m, path + [m])
        state[n] = 2
    for n in graph:
        if not state.get(n):
            visit(n, [n])
    return out


def shown(v: object) -> str:
    """A value short enough for a change line, or "" when it is not."""
    if v is None:
        return "none"
    s = v if isinstance(v, str) else json.dumps(v)
    if s == "":
        return "''"
    return s if len(s) <= 48 and "\n" not in s else ""


def change(label: str, a: object, b: object) -> str:
    if a is None:
        return f"{label} set to {shown(b)}" if shown(b) else f"{label} set"
    if b is None:
        return f"{label} removed"
    return f"{label} {shown(a)} -> {shown(b)}" if shown(a) and shown(b) else f"{label} changed"


def dict_changes(label: str, a: "dict | None", b: "dict | None") -> "list[str]":
    a, b = a or {}, b or {}
    return [change(f"{label}{k}", a.get(k), b.get(k)) for k in sorted(set(a) | set(b))
            if (str(a[k]) if k in a else None) != (str(b[k]) if k in b else None)]


def step_changes(a: dict, b: dict) -> "list[str]":
    """One phrase per field of a step that differs: "effort high -> xhigh"."""
    return [change(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if k != "id" and a.get(k) != b.get(k)]


def steps_changes(pid: str, old: "list[dict]", new: "list[dict]") -> "list[str]":
    oi, ni = [s.get("id") for s in old], [s.get("id") for s in new]
    od, nd = {s.get("id"): s for s in old}, {s.get("id"): s for s in new}
    out = [f"{pid}: step {sid} removed" for sid in oi if sid not in nd]
    for i, sid in enumerate(ni):
        if sid not in od:
            out.append(f"{pid}: step {sid} added " + (f"after {ni[i - 1]}" if i else "first"))
        else:
            out += [f"{pid}: {sid} {x}" for x in step_changes(od[sid], nd[sid])]
    if [s for s in oi if s in nd] != [s for s in ni if s in od]:
        out.append(f"{pid}: steps reordered: {', '.join(str(s) for s in ni)}")
    return out


def pr_added(p: dict) -> str:
    return f"{p['id']} added" + (f" after {', '.join(p['after'])}" if p.get("after") else "")


def flow_changes(S: State, old: "dict | None", new: dict) -> "list[str]":
    """What a new version changes, in plain words, one line each. Empty: the file is the version it would replace."""
    if old is None:
        return [f"flow created: {len(new['prs'])} PRs · slots {new['slots']} · start {new['start']}"] + [pr_added(p) for p in new["prs"]]
    out = [change(k, old.get(k), new.get(k)) for k in ("title", "slots", "start") if old.get(k) != new.get(k)]
    ro, rn = old["resolved"], new["resolved"]
    if (ro.get("profile") or {}).get("path") != (rn.get("profile") or {}).get("path"):   # the file, not its spelling
        out.append(change("profile", old.get("profile") if ro.get("profile") else None, new.get("profile") if rn.get("profile") else None))
    if (ro.get("env") or {}).get("SCRATCH") != (rn.get("env") or {}).get("SCRATCH"):
        out.append(change("SCRATCH from the environment", (ro.get("env") or {}).get("SCRATCH"), (rn.get("env") or {}).get("SCRATCH")))
    out += dict_changes("profile: vars.", (ro.get("profile") or {}).get("vars"), (rn.get("profile") or {}).get("vars"))
    to, tn = ro.get("templates") or {}, rn.get("templates") or {}
    for name in sorted(set(to) | set(tn)):
        a, b = to.get(name), tn.get(name)
        if a is None or b is None or a["path"] != b["path"]:
            out.append(change(f"templates.{name}", (a or {}).get("path"), (b or {}).get("path")))
        if a and b:
            out += dict_changes(f"templates.{name}: vars.", a["def"].get("vars"), b["def"].get("vars"))
            out += [f"templates.{name}: {x}" for x in step_changes({k: v for k, v in a["def"].items() if k not in ("steps", "vars")},
                                                                    {k: v for k, v in b["def"].items() if k not in ("steps", "vars")})]
    out += dict_changes("vars.", old.get("vars"), new.get("vars"))
    before = {str(p["id"]).lower(): p for p in old["prs"]}
    now_ids = {str(p["id"]).lower() for p in new["prs"]}
    out += [f"{p['id']} removed (not started)" for p in old["prs"] if str(p["id"]).lower() not in now_ids]
    for p in new["prs"]:
        q = before.get(p["id"].lower())
        if q is None:
            out.append(pr_added(p))
            continue
        pid = p["id"]
        if q["id"] != pid:
            out.append(f"{q['id']} renamed {pid}")
        out += [change(f"{pid}: {k}", q.get(k), p.get(k)) for k in ("part", "title", "base", "template") if q.get(k) != p.get(k)]
        if sorted(x.lower() for x in q.get("after") or []) != sorted(x.lower() for x in p.get("after") or []):   # a set
            out.append(change(f"{pid}: after", ", ".join(q.get("after") or []) or "-", ", ".join(p.get("after") or []) or "-"))
        out += dict_changes(f"{pid}: vars.", q.get("vars"), p.get("vars"))
        if isinstance(q.get("steps"), list) != isinstance(p.get("steps"), list):
            out.append(f"{pid}: " + ("its own steps now" if isinstance(p.get("steps"), list) else "the template's steps again"))
        try:
            old_steps = flow_effective(S, old, q)["def"]["steps"]
        except Exception:
            old_steps = []
        out += steps_changes(pid, old_steps, flow_effective(S, new, p)["def"]["steps"])
    # The order is part of the version: the scheduler starts ready PRs in it, and show and progress list them in it.
    if [i for i in (str(p["id"]).lower() for p in old["prs"]) if i in now_ids] != [i for i in (p["id"].lower() for p in new["prs"]) if i in before]:
        out.append(f"PRs reordered: {', '.join(p['id'] for p in new['prs'])}")
    return out


def step_names(kit: Path, d: dict) -> "set[str]":
    """The variables a step uses: the {NAME}s of its definition and of its spec, WT and KIT for a worker step (its
    HEAD range is measured in WT, its spec comes from the kit), KIT for a step with a command (it runs there)."""
    texts = [d.get(k) for k in ("agent", "model", "effort", "worktree", "when", "run", "title")]
    texts += list(d.get("checks") or []) + list(d.get("show") or [])
    names = {m.group(1) for x in texts if isinstance(x, str) for m in VAR_RE.finditer(x)}
    if d.get("type", "worker") == "worker":
        names |= {"WT", "KIT"}
        spec = kit / "specs" / str(d.get("spec", ""))
        if d.get("spec") and spec.is_file():
            names |= {m.group(1) for m in VAR_RE.finditer(spec.read_text())}
    if any(d.get(k) for k in ("when", "run", "checks", "show")):
        names.add("KIT")
    return names


def settled_head(st: dict) -> int:
    """How many of a chain's steps are fixed: up to its last step that is not pending, or that its runner has claimed
    because it is about to start it. A pending step in between (a retried step of a group) keeps its place."""
    claimed = set((st.get("flow") or {}).get("claimed") or [])
    return 1 + max((i for i, s in enumerate(st["steps"]) if s["status"] != "pending" or s["id"] in claimed), default=-1)


def flow_chain_problems(S: State, old: "dict | None", new: dict) -> "list[str]":
    """What a new version may not do to PRs whose chain exists: one line per refused change."""
    out = []
    for path in sorted((S / "chains").glob("*/state.json")):
        st = read_json(path)
        if not st or not st.get("pr"):
            continue
        pid, p = st["pr"], flow_pr(new, st["pr"])
        if not st.get("flow"):
            if p is not None:
                out.append(f"{pid}: its chain was started with router.py chain, so the flow cannot take it over; give the flow's PR another id")
            continue
        if p is None:
            out.append(f"{pid}: its chain exists ({st.get('status')}), so it stays in the flow")
            continue
        if p["id"] != pid:
            out.append(f"{pid}: its chain exists, so its id stays {pid} (not {p['id']})")
        q = flow_pr(old, pid)
        if q is not None and sorted(x.lower() for x in q.get("after") or []) != sorted(x.lower() for x in p.get("after") or []):
            out.append(f"{pid}: its chain exists, so its after cannot change ({', '.join(q.get('after') or []) or '-'} -> {', '.join(p.get('after') or []) or '-'})")
        if p.get("template") not in (new["resolved"].get("templates") or {}):
            continue
        eff = flow_effective(S, new, p)
        steps, k = st["steps"], settled_head(st)
        claimed = set(st["flow"].get("claimed") or [])
        new_steps = eff["def"]["steps"]
        new_ids = [s.get("id") for s in new_steps]
        for i, s in enumerate(steps[:k]):
            fixed = s["status"] != "pending" or s["id"] in claimed
            what = f"step {s['id']} is {s['status'].replace('_', ' ') if s['status'] != 'pending' else 'about to start'}" if fixed \
                else f"step {s['id']} comes before a settled step"
            if s["id"] not in new_ids:
                out.append(f"{pid}: {what}: it cannot be removed")
            elif new_ids.index(s["id"]) != i:
                out.append(f"{pid}: {what}: the steps up to it cannot be reordered, and no step can go before it")
            elif fixed and new_steps[i] != s["def"]:
                out.append(f"{pid}: {what}: its definition cannot change ({'; '.join(step_changes(s['def'], new_steps[i]))})")
        keys = set(st["flow"].get("vars_keys") or [])
        users: "dict[str, list[str]]" = {}
        for s in steps[:k]:
            if s["status"] != "pending" or s["id"] in claimed:
                for n in step_names(Path(st.get("kit_dir") or st["def_dir"]), s["def"]):
                    users.setdefault(n, []).append(s["id"])
        for n in sorted(users):
            if n not in keys and n in st["vars"] or n in STEP_VARS_EARLY + STEP_VARS_LATE + ("NOTE",) or n.startswith("HEAD_"):
                continue   # an export, a HEAD_* or a step's own value: the run sets it, the flow cannot change it
            a = st["vars"].get(n) if n in keys else None
            b = eff["vars"].get(n)
            if a != b:
                out.append(f"{pid}: {change(n, a, b)}, and settled step(s) {', '.join(users[n])} used it")
    return out


def flow_apply(S: State, raw: object, base_dir: Path, base: "int | None" = None, by: str = "coordinator", note: str = "",
               dry_run: bool = False) -> dict:
    """The only way a flow enters or changes a run. Accepted: v+1, one history row, one journal line, the page, and a
    scheduler pass. Refused: one line per problem, and nothing changed. A file the same as the current version: no-op.
    dry_run: every check and the change lines of an accepted apply, and nothing written (the page shows them first)."""
    result = {"ok": False, "version": 0, "changes": [], "problems": [], "started": [], "stale": False, "dry_run": dry_run}
    with flow_lock(S):
        try:
            old = load_flow(S)
        except RouterError as e:
            return dict(result, problems=[f"{e}; fix or remove {S / 'flow.json'}"])
        cur = old["version"] if old else 0
        result["version"] = cur
        if base is not None and base != cur:
            return dict(result, problems=[f"stale: the run is at v{cur}"], stale=True)
        new, problems = flow_build(S, raw, base_dir)
        if old and new and not new["resolved"]["env"]:   # applied from a terminal without SCRATCH: the run keeps its own
            new["resolved"]["env"] = dict(old["resolved"].get("env") or {})
        if not problems:
            problems = flow_chain_problems(S, old, new)
        if problems:
            return dict(result, problems=problems)
        changes = flow_changes(S, old, new)
        if not changes or dry_run:
            return dict(result, ok=True, changes=changes)
        v = cur + 1
        ids = {p["id"].lower() for p in new["prs"]}
        new.update(version=v, history=list((old or {}).get("history") or []) + [{"v": v, "at": now(), "by": by, "note": note, "changes": changes}],
                   removed=[x for x in (old or {}).get("removed") or [] if x["id"].lower() not in ids]
                   + [{"id": p["id"], "part": p.get("part") or "", "title": p.get("title") or "", "v": v}
                      for p in (old or {}).get("prs") or [] if p["id"].lower() not in ids])
        write_json(S / "flow.json", new)
        journal(S, "", "", "", "", f"flow v{v} by {by}: {len(changes)} changes" + (f"; {note}" if note else "") + " · " + "; ".join(changes))
        try:
            started = flow_schedule(S)
        except Exception as e:
            started = [f"the scheduler pass failed: {e!r}"]
    refresh_progress(S)
    return dict(result, ok=True, version=v, changes=changes, started=started)


def flow_start_chain(S: State, f: dict, p: dict) -> int:
    """Create a flow PR's chain exactly as `chain` would, and start its runner. Called under the flow lock."""
    pid, cdir = p["id"], chain_dir(S, p["id"])
    eff = flow_effective(S, f, p)
    if pid_alive(cdir / "runner.pid"):
        raise RouterError(f"{pid}: its runner is already running")
    cdir.mkdir(parents=True, exist_ok=True)
    def_path = Path(eff["def_path"])
    if eff["override"]:   # the chain gets a file of its own steps; the kit stays the template's
        def_path = cdir / "def.json"
        write_json(def_path, dict(eff["def"], kit=eff["kit"]))
    steps = eff["def"]["steps"]
    st = {"pr": pid, "def": str(def_path), "def_dir": eff["def_dir"], "kit_dir": eff["kit"], "created": now(), "status": "running",
          "vars": dict(eff["vars"]), "steps": [{"id": s["id"], "def": s, "status": "pending", "attempts": []} for s in steps],
          "flow": {"version": f["version"], "started_at": f["version"], "vars_keys": sorted(eff["vars"]),
                   "created": {s["id"]: def_sha(s) for s in steps}, "claimed": []}}
    save_chain(S, st)
    journal(S, pid, "", "", "", f"chain created from {def_path.name}: {len(steps)} steps")
    journal(S, pid, "", "", "", f"flow: started {pid} (v{f['version']})")
    runner = launch(S, ["run", pid], cdir / "runner.log")
    atomic_write(cdir / "runner.pid", f"{runner}\n")
    return runner


def flow_schedule(S: State, only: str = "") -> "list[str]":
    """One scheduler pass: under "start": "auto", start every PR that is ready, in the flow's order. Ready: no chain
    yet, every PR it is after is done, a slot is free, and its definition has a value for every variable. With
    only=<pr>, that PR alone and whatever "start" says; a PR that is not ready raises RouterError saying why."""
    if not (S / "flow.json").exists():   # a run without a flow: not even the lock file
        if only:
            raise RouterError("no flow: router.py flow apply <file>")
        return []
    with flow_lock(S):
        f = load_flow(S)
        if f is None:
            return []
        if only and flow_pr(f, only) is None:
            raise RouterError(f"{only}: the flow v{f['version']} does not name it")
        if not only and f.get("start") != "auto":
            return []
        if not pid_alive(S / "mailbox.pid"):
            if only:
                raise RouterError("the mailbox daemon is not running: router.py init")
            return ["nothing was started: the mailbox daemon is not running (router.py init)"]
        lines = []
        for p in f["prs"]:
            pid = p["id"]
            if only and pid.lower() != only.lower():
                continue
            st = read_json(chain_dir(S, pid) / "state.json")
            if st is not None:
                if only:
                    raise RouterError(f"{pid}: its chain exists ({st.get('status')}): router.py status")
                continue
            why = flow_waits(S, f, p)
            opened = open_chains(S)
            if not why and len(opened) >= f["slots"]:
                why = f"no free slot ({len(opened)} of {f['slots']} open: {', '.join(opened)})"
            if why:
                if only:
                    raise RouterError(f"{pid} is not ready: {why}")
                continue
            try:
                runner = flow_start_chain(S, f, p)
            except (RouterError, OSError) as e:
                key = f"flowstart-{pid}-{hashlib.sha1(str(e).encode()).hexdigest()[:10]}"
                if not (S / "wake" / "keys" / safe(key)).exists():
                    journal(S, pid, "", "", "", f"flow: could not start {pid}: {e}")
                wake(S, "start", pid, [f"WAKE start · {pid}: the flow could not start its chain: {e}",
                                       f"next: router.py flow show  |  router.py flow start {pid}"], key=key)
                if only:
                    raise RouterError(f"{pid}: the chain could not be started: {e}")
                continue
            lines.append(f"started {pid} (v{f['version']}), runner pid {runner}")
        return lines


def flow_tick(S: State) -> None:
    """The daemon's scheduler pass. A flow it cannot read rings once per reason; the daemon goes on."""
    try:
        for line in flow_schedule(S):
            if line.startswith("started"):
                print(f"{now()} flow: {line}", flush=True)
    except Exception as e:
        print(f"{now()} flow: the scheduler pass failed: {e!r}", flush=True)
        wake(S, "daemon", "", [f"WAKE daemon · {e}" if isinstance(e, RouterError) else f"WAKE daemon · the flow could not be read: {e!r}",
                               "no PR of the flow is started until it can be read; running chains go on",
                               "next: router.py flow show, then router.py flow apply <a fixed file>"],
             key=f"flow-{hashlib.sha1(str(e).encode()).hexdigest()[:10]}")


def flow_reread(S: State, st: dict) -> None:
    """At a step boundary: the chain's steps that are not settled become the flow's latest ([settled steps as they
    are] + [the flow's other steps, in its order, with its definitions]), and its variables the flow's, except those
    a step exported and the router's HEAD_*. Raises RouterError("the flow could not be read: …")."""
    try:
        f = load_flow(S)
        if f is None:
            raise RouterError("the flow could not be read: there is no flow.json")
        p = flow_pr(f, st["pr"])
        if p is None:
            raise RouterError(f"the flow could not be read: v{f['version']} does not name {st['pr']}")
        eff = flow_effective(S, f, p)
    except RouterError:
        raise
    except Exception as e:
        raise RouterError(f"the flow could not be read: {e!r}")
    fl, v, pr = st["flow"], f["version"], st["pr"]
    steps = st["steps"]
    k = settled_head(st)
    defs = {d.get("id"): d for d in eff["def"]["steps"]}
    head = steps[:k]
    for s in head:
        if s["status"] == "pending" and s["id"] in defs and s["def"] != defs[s["id"]]:
            journal(S, pr, s["id"], "", "", f"flow v{v}: step edited: {'; '.join(step_changes(s['def'], defs[s['id']]))}")
            s["def"] = defs[s["id"]]
    rest = {s["id"]: s for s in steps[k:]}
    order_before = [s["id"] for s in steps[k:]]
    new = list(head)
    for d in eff["def"]["steps"]:
        if any(s["id"] == d.get("id") for s in head):
            continue
        s = rest.pop(d["id"], None)
        if s is None:
            s = {"id": d["id"], "def": d, "status": "pending", "attempts": []}
            journal(S, pr, d["id"], "", "", f"flow v{v}: step added after {new[-1]['id'] if new else 'nothing (it is first)'}")
        elif s["def"] != d:
            journal(S, pr, d["id"], "", "", f"flow v{v}: step edited: {'; '.join(step_changes(s['def'], d))}")
            s["def"] = d
        new.append(s)
    for s in rest.values():
        journal(S, pr, s["id"], "", "", f"flow v{v}: step removed; it will not run")
        if s["attempts"]:
            fl.setdefault("removed_steps", []).append({"id": s["id"], "v": v, "attempts": s["attempts"]})
    order_after = [s["id"] for s in new[k:] if s["id"] in order_before]
    if order_after != [x for x in order_before if x in order_after]:
        journal(S, pr, "", "", "", f"flow v{v}: steps reordered: {', '.join(s['id'] for s in new[k:])}")
    st["steps"] = new
    keys = set(fl.get("vars_keys") or [])
    runtime = {n: val for n, val in st["vars"].items() if n not in keys or n.startswith(("HEAD_BEFORE_", "HEAD_AFTER_"))}
    st["vars"] = dict(eff["vars"], **runtime)
    fl["vars_keys"] = sorted(n for n in eff["vars"] if n not in runtime)
    st.update({"kit_dir": eff["kit"], "def_dir": eff["def_dir"], "def": eff["def_path"]})
    if eff["override"]:
        own = dict(eff["def"], kit=eff["kit"])
        st["def"] = str(chain_dir(S, pr) / "def.json")
        if read_json(Path(st["def"])) != own:
            write_json(Path(st["def"]), own)
    fl["version"] = v


def flow_stamp(st: dict, step: dict, att: dict) -> None:
    """A flow chain's attempt records the flow version it ran under, why it ran, and whether its step was edited."""
    fl = st.get("flow")
    if fl:
        att.update(flow_version=fl.get("version"), cause=step.get("cause") or ("first" if att["n"] == 1 else "retry"))
        if def_sha(step["def"]) != (fl.get("created") or {}).get(step["id"]):
            att["edited"] = True


def flow_state(S: State, f: dict, p: dict, opened: "list[str]") -> str:
    """A PR's state for flow show: its chain's, or what it waits for."""
    st = read_json(chain_dir(S, p["id"]) / "state.json")
    if st is not None:
        row = pr_row(S, {"id": p["id"]}, st)
        return f"running {' ∥ '.join(row['at'])}" if row["state"] == "running" else row["state"]
    why = flow_waits(S, f, p)
    if why:
        return why if why.startswith(("waiting", "cannot")) else f"not started · {why}"
    if f.get("start") != "auto":
        return f"ready · start manual: router.py flow start {p['id']}"
    return "ready" + ("" if len(opened) < f["slots"] else " · no free slot")


def cmd_flow(S: State, a: argparse.Namespace) -> int:
    if a.flow_cmd == "apply":
        try:
            raw = json.loads(Path(a.file).read_text())
        except (OSError, ValueError) as e:
            print(f"NOT OK flow: 1 problem(s), nothing changed\n  - {a.file} could not be read: {e}")
            return 1
        r = flow_apply(S, raw, Path(a.file).resolve().parent, base=a.base, by=a.by, note=a.note, dry_run=a.dry_run)
        if not r["ok"]:
            print(f"NOT OK flow: {len(r['problems'])} problem(s), nothing changed")
            print("\n".join(f"  - {x}" for x in r["problems"]))
            return 1
        if a.dry_run and r["changes"]:
            print(f"OK flow v{r['version']} -> v{r['version'] + 1}, dry run, nothing written: {len(r['changes'])} changes")
            print("\n".join(f"  - {x}" for x in r["changes"]))
            return 0
        if not r["changes"]:
            print(f"OK flow v{r['version']}: no change; the file is v{r['version']} as it is, so the version stays")
            return 0
        print(f"OK flow v{r['version']}: {len(r['changes'])} changes")
        print("\n".join([f"  - {x}" for x in r["changes"]] + [f"  {x}" for x in r["started"]]))
        return 0
    f = load_flow(S)
    if f is None:
        die("no flow: router.py flow apply <file>")
    if a.flow_cmd == "show":
        opened = open_chains(S)
        print(f"flow v{f['version']} · slots {len(opened)}/{f['slots']} · start {f['start']}" + (f" · {f['title']}" if f.get("title") else ""))
        for p in f["prs"]:
            print("  " + " · ".join([p["id"], str(p.get("part") or "-"), str(p.get("title") or "-"),
                                     "after " + (", ".join(p.get("after") or []) or "-"), flow_state(S, f, p, opened)]))
        for x in f.get("removed") or []:
            print("  " + " · ".join([x["id"], x.get("part") or "-", x.get("title") or "-", f"removed in v{x['v']}"]))
        return 0
    if a.flow_cmd == "start":
        lines = flow_schedule(S, only=a.pr)
        refresh_progress(S)
        print(f"OK {lines[0]}" if lines else f"NOT OK {a.pr}: nothing was started")
        return 0 if lines else 1
    for h in f.get("history", [])[-max(1, a.n):]:
        print(f"v{h['v']} · {h['at']} · by {h['by']} · {len(h['changes'])} changes" + (f" · {h['note']}" if h.get("note") else ""))
        print("\n".join(f"  - {x}" for x in h["changes"]))
    return 0


# ---------------------------------------------------------------- the page

PORT = int(os.environ.get("ROUTER_PORT", "0"))
PAGE_DIR = HERE / "page"
PAGE_FILES = {"index.html": "text/html; charset=utf-8", "page.js": "text/javascript; charset=utf-8",
              "page.css": "text/css; charset=utf-8"}
BODY_MAX = 2_000_000   # bytes of a POST /flow body


def palette(f: dict) -> "list[dict]":
    """The roles the page can drop into a PR: every spec in the kits of the flow's templates, then a gate and a script.
    A spec's step is copied from the first template step that names it (the flow's templates first, then the kit's
    own templates/), so a dropped role comes with its usual agent, model, effort and checks."""
    kits, defs = [], []
    for t in (f["resolved"].get("templates") or {}).values():
        if t["kit"] not in kits:
            kits.append(t["kit"])
        defs.append(t["def"])
    for kit in kits:
        for path in sorted(Path(kit).glob("templates/*.json")):
            d = read_json(path)
            if isinstance(d, dict) and isinstance(d.get("steps"), list):
                defs.append(d)
    out, seen = [], set()
    for kit in kits:
        for spec in sorted(Path(kit).glob("specs/*.md")):
            if spec.name in seen:
                continue
            seen.add(spec.name)
            proto = next((s for d in defs for s in d["steps"] if isinstance(s, dict) and s.get("spec") == spec.name), None)
            step = dict(proto) if proto else {"agent": "claude", "spec": spec.name}
            step["id"] = step_var(spec.stem).lower()
            out.append({"role": spec.stem, "step": step})
    out.append({"role": "gate", "step": {"id": "gate", "type": "gate", "title": "the coordinator decides"}})
    out.append({"role": "script", "step": {"id": "script", "type": "script", "run": "true"}})
    return out


def page_state(S: State) -> dict:
    """GET /state: progress_data, and the flow as the canvas draws it. Each PR carries the steps the flow gives it
    (its own or its template's) and how many of them are fixed: up to its chain's last settled step, nothing of a PR
    that has not started."""
    d = progress_data(S)
    try:
        f = load_flow(S)
    except RouterError:
        f = None
    if f is None:
        return d
    prs = []
    for p in f["prs"]:
        q = {k: p.get(k) for k in ("id", "part", "title", "base", "after", "template", "vars")}
        try:
            q["steps"] = flow_effective(S, f, p)["def"]["steps"]
        except Exception:   # a copy the router cannot read: the scheduler rings for it
            q["steps"] = []
        st = read_json(chain_dir(S, p["id"]) / "state.json")
        q["own_steps"] = isinstance(p.get("steps"), list)
        q["started"] = st is not None
        q["fixed"] = settled_head(st) if st and st.get("flow") else len(q["steps"]) if st else 0
        q["claimed"] = list(((st or {}).get("flow") or {}).get("claimed") or [])
        prs.append(q)
    d["flow"] = dict(d["flow"] or {}, title=f.get("title") or "", prs=prs, removed=f.get("removed") or [],
                     templates=sorted((f["resolved"].get("templates") or {})), palette=palette(f),
                     history=(f.get("history") or [])[-5:])
    return d


def page_url(S: State) -> str:
    """The page's URL while the mailbox daemon that serves it is alive; "" otherwise."""
    info = read_json(S / "page.json") or {}
    pid = pid_alive(S / "mailbox.pid")
    return str(info.get("url") or "") if pid and info.get("pid") == pid else ""


class PageHandler(http.server.BaseHTTPRequestHandler):
    """The page's four routes. A handler that raises answers 500 and is logged; it never reaches the mail loop."""
    S: State
    server_version = "router"
    protocol_version = "HTTP/1.0"   # one request per connection
    timeout = 30                    # a client that stops sending frees its thread

    def log_message(self, fmt: str, *args: object) -> None:   # no access log; errors and applies are logged below
        pass

    def send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def answer(self, code: int, obj: object) -> None:
        self.send(code, json.dumps(obj).encode(), "application/json")

    def send_error(self, code: int, message: "str | None" = None, explain: "str | None" = None) -> None:
        super().send_error(404 if code == 501 else code, message, explain)   # an unknown method: no such route

    def local(self) -> bool:
        """Only this machine's own pages: the Host is 127.0.0.1 or localhost on our port (a DNS rebinding cannot
        pass), and a POST from a page comes from this origin."""
        port = self.server.server_address[1]
        hosts = (f"127.0.0.1:{port}", f"localhost:{port}")
        origin = self.headers.get("Origin")
        return self.headers.get("Host", "") in hosts and (origin is None or origin in tuple(f"http://{h}" for h in hosts))

    def guard(self, fn) -> None:
        try:
            if not self.local():
                self.answer(403, {"ok": False, "reason": "the page answers http://127.0.0.1 and http://localhost only"})
                return
            fn()
        except Exception as e:
            print(f"{now()} page: {self.command} {self.path} raised {e!r}\n{traceback.format_exc()}", flush=True)
            with contextlib.suppress(Exception):
                self.answer(500, {"ok": False, "reason": f"the router could not answer: {e!r} (mailbox.log)"})

    def do_GET(self) -> None:
        self.guard(self.get)

    def do_POST(self) -> None:
        self.guard(self.post)

    def get(self) -> None:
        path = self.path.split("?", 1)[0]
        name = "index.html" if path == "/" else path[len("/page/"):] if path.startswith("/page/") else ""
        if name in PAGE_FILES:
            self.send(200, (PAGE_DIR / name).read_bytes(), PAGE_FILES[name])
        elif path == "/state":
            self.answer(200, page_state(self.S))
        elif path == "/flow":
            f = load_flow(self.S)
            if f is None:
                self.answer(404, {"ok": False, "reason": "no flow: router.py flow apply <file>"})
            else:
                self.answer(200, f)
        else:
            self.answer(404, {"ok": False, "reason": f"no such page: {path}"})

    def post(self) -> None:
        if self.path.split("?", 1)[0] != "/flow":
            self.answer(404, {"ok": False, "reason": f"no such page: {self.path}"})
            return
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            self.answer(415, {"ok": False, "reason": "send the body as application/json"})
            return
        size = int(self.headers.get("Content-Length") or 0)
        if not 0 < size <= BODY_MAX:
            self.answer(413 if size else 411, {"ok": False, "reason": f"a body of 1 to {BODY_MAX} bytes, with its Content-Length"})
            return
        try:
            body = json.loads(self.rfile.read(size))   # read whole before the flow lock is taken
        except ValueError as e:
            self.answer(422, {"ok": False, "problems": [f"the body is not JSON: {e}"]})
            return
        if not isinstance(body, dict) or isinstance(body.get("base"), bool) or not isinstance(body.get("base"), int) \
                or "flow" not in body:
            self.answer(422, {"ok": False, "problems": ['the body is {"base": <the version the edit started from>, "by", "note", "flow": {...}}']})
            return
        S = self.S
        try:
            old = load_flow(S)
        except RouterError:
            old = None   # flow_apply refuses it with the reason
        by, note = oneline(body.get("by") or "page", 80), oneline(body.get("note") or "", 400)
        r = flow_apply(S, body["flow"], Path(old["dir"]) if old and old.get("dir") else S.root, base=body["base"], by=by, note=note,
                       dry_run=bool(body.get("dry_run")))
        if not r["dry_run"]:
            print(f"{now()} page: flow apply by {by} on v{body['base']}: " + (
                f"v{r['version']}, {len(r['changes'])} changes" if r["ok"] else f"refused: {'; '.join(r['problems'])[:600]}"), flush=True)
        if r["ok"]:
            self.answer(200, {"ok": True, "version": r["version"], "changes": r["changes"], "started": r["started"], "dry_run": r["dry_run"]})
        elif r["stale"]:
            self.answer(409, {"ok": False, "reason": r["problems"][0], "current": load_flow(S)})
        else:
            self.answer(422, {"ok": False, "problems": r["problems"]})


def serve_page(S: State) -> "http.server.ThreadingHTTPServer | None":
    """Start the page's server on 127.0.0.1 in a thread of the mailbox daemon and write page.json. A port that
    cannot be bound is logged: the daemon goes on without the page."""
    handler = type("Handler", (PageHandler,), {"S": S})
    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    except OSError as e:
        print(f"{now()} page: not served, 127.0.0.1:{PORT} could not be bound: {e}", flush=True)
        (S / "page.json").unlink(missing_ok=True)
        return None
    srv.daemon_threads = True
    host, port = srv.server_address[:2]
    threading.Thread(target=srv.serve_forever, name="page", daemon=True).start()
    write_json(S / "page.json", {"url": f"http://{host}:{port}/", "host": host, "port": port, "pid": os.getpid(), "started": now()})
    print(f"{now()} page: http://{host}:{port}/", flush=True)
    return srv


def cmd_page(S: State, a: argparse.Namespace) -> int:
    url = page_url(S)
    if not url:
        die("the page is not served: " + ("the mailbox daemon is not running (router.py init)" if not pid_alive(S / "mailbox.pid")
                                          else f"the mailbox daemon has no page; tail {S / 'mailbox.log'}"))
    print(url)
    if a.open:
        d, raw = orca(S, ["tab", "create", "--url", url], timeout=60, caller=True)
        if orca_error(d):
            die(f"NOT OK orca tab create answered {orca_error(d)}: {oneline(raw, 300)}")
        print("OK opened in an Orca browser tab")
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0 if len(sys.argv) >= 2 else 2
    ap = argparse.ArgumentParser(prog="router.py", add_help=False)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("init"); p.add_argument("--run")
    sub.add_parser("mailbox")
    p = sub.add_parser("chain"); p.add_argument("pr"); p.add_argument("--def", dest="definition", required=True)
    p.add_argument("--dry-run", action="store_true"); p.add_argument("vars", nargs="*")
    p = sub.add_parser("run"); p.add_argument("pr")
    p = sub.add_parser("wait"); p.add_argument("--timeout-min", type=float, default=0)
    p = sub.add_parser("resume"); p.add_argument("pr"); p.add_argument("--accept"); p.add_argument("--from", dest="from_step")
    p.add_argument("--note"); p.add_argument("--adopt"); p.add_argument("--set", action="append")
    p = sub.add_parser("fail"); p.add_argument("pr"); p.add_argument("--step"); p.add_argument("--why", required=True)
    p = sub.add_parser("retry"); p.add_argument("pr"); p.add_argument("--note"); p.add_argument("--agent")
    p.add_argument("--model"); p.add_argument("--effort")
    p = sub.add_parser("reply"); p.add_argument("message"); p.add_argument("answer")
    p = sub.add_parser("worker"); p.add_argument("label"); p.add_argument("--spec"); p.add_argument("--spec-file")
    p.add_argument("--agent", required=True); p.add_argument("--model"); p.add_argument("--effort")
    p.add_argument("--worktree", default="current"); p.add_argument("--pr")
    sub.add_parser("status")
    p = sub.add_parser("last"); p.add_argument("n", nargs="?", type=int, default=1)
    sub.add_parser("workers")
    sub.add_parser("progress")
    p = sub.add_parser("plan"); p.add_argument("file")
    p = sub.add_parser("stop"); p.add_argument("pr", nargs="?"); p.add_argument("--all", action="store_true")
    p = sub.add_parser("page"); p.add_argument("--open", action="store_true")
    p = sub.add_parser("collect"); g = p.add_mutually_exclusive_group(); g.add_argument("--all", action="store_true")
    g.add_argument("--pr"); g.add_argument("--dispatch"); p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    fs = sub.add_parser("flow").add_subparsers(dest="flow_cmd")
    p = fs.add_parser("apply"); p.add_argument("file"); p.add_argument("--base", type=lambda x: int(x.lstrip("v")))
    p.add_argument("--by", default="coordinator"); p.add_argument("--note", default="")
    p.add_argument("--dry-run", action="store_true")
    fs.add_parser("show")
    p = fs.add_parser("start"); p.add_argument("pr")
    p = fs.add_parser("history"); p.add_argument("n", nargs="?", type=int, default=10)
    try:
        a, extra = ap.parse_known_args()
    except SystemExit:
        return 2
    if not a.cmd or a.cmd == "flow" and not a.flow_cmd:
        print(__doc__.strip())
        return 2
    # argparse before Python 3.12 does not give `chain` the K=V words that follow --def: they arrive here.
    if extra and a.cmd == "chain" and not any(x.startswith("-") for x in extra):
        a.vars += extra
    elif extra:
        print(f"router.py {a.cmd}: unrecognized arguments: {' '.join(extra)}", file=sys.stderr)
        return 2
    S = State()
    try:
        return dispatch(S, a)
    except RouterError as e:
        print(str(e), file=sys.stderr)
        return 1
    finally:
        flush_progress(S)   # resume, retry, fail and chain saved a chain: the page follows before the command ends


def dispatch(S: State, a: argparse.Namespace) -> int:
    return {"init": cmd_init, "mailbox": cmd_mailbox, "chain": cmd_chain, "run": cmd_run, "wait": cmd_wait,
            "resume": cmd_resume, "retry": cmd_retry, "reply": cmd_reply, "worker": cmd_worker, "status": cmd_status,
            "workers": cmd_workers, "stop": cmd_stop, "last": cmd_last, "fail": cmd_fail, "progress": cmd_progress,
            "plan": cmd_plan, "flow": cmd_flow, "page": cmd_page, "collect": cmd_collect}[a.cmd](S, a)


if __name__ == "__main__":
    sys.exit(main())
