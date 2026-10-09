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

Env:
  ROUTER_STATE          state directory (default $SCRATCH/router)
  ORCA_CLI_COMMAND      the Orca CLI (default orca)
  ROUTER_WAIT_MS        one mailbox long-poll (default 120000)
  ROUTER_SILENT_MIN     minutes without a heartbeat before a live worker rings as silent (default 20)
  ROUTER_START_TIMEOUT_MS  worker-start --timeout-ms (default 300000)
  ROUTER_MAX_CHAINS     chains allowed to run at once (default 2)
  ROUTER_POLL_S         file poll interval (default 3)
  ROUTER_REGISTRY_WAIT_S   how long a message waits for its worker's start receipt to be recorded (default 10)

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
  progress.html               the owner's view. Rewritten at every change, and it reloads itself in the browser
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORCA = os.environ.get("ORCA_CLI_COMMAND", "orca")
WAIT_MS = int(os.environ.get("ROUTER_WAIT_MS", "120000"))
SILENT_MIN = float(os.environ.get("ROUTER_SILENT_MIN", "20"))
START_TIMEOUT_MS = int(os.environ.get("ROUTER_START_TIMEOUT_MS", "300000"))
MAX_CHAINS = int(os.environ.get("ROUTER_MAX_CHAINS", "2"))
POLL_S = float(os.environ.get("ROUTER_POLL_S", "3"))
REGISTRY_WAIT_S = float(os.environ.get("ROUTER_REGISTRY_WAIT_S", "10"))

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
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
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
    ack, lost, unreadable = "", 0, 0
    while not _stop:
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
            elif s.get("readonly") and not git_head(variables["WT"])[0]:
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
    if len(open_chains(S)) >= MAX_CHAINS:
        die(f"{len(open_chains(S))} chains are open ({', '.join(open_chains(S))}) and ROUTER_MAX_CHAINS is {MAX_CHAINS}: {a.pr} was not started")
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
    rc, _line, _ = run_line(render(when, step_vars(st, step), shell=True), st["def_dir"], 60, orca_env(S))
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
    attempt.update(task=res.get("taskId", ""), dispatch=res["dispatchId"])
    launch_info = res.get("launch") or {}
    if launch_info.get("requested") != launch_info.get("effective"):
        attempt["launch_differs"] = f"asked {launch_info.get('requested')}, got {launch_info.get('effective')}"
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
        rc, line, _ = run_line(render(c, v, shell=True), st["def_dir"], float(d.get("check_timeout", 300)), orca_env(S))
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
                    step["attempts"].append({"n": len(step["attempts"]) + 1, "started": now()})
                    step["status"] = "script"   # saved, so status and progress show it: a CI wait can take an hour
                    save_chain(S, st)
                    flush_progress(S)
                    rc, line, exports = run_line(cmd, st["def_dir"], float(step["def"].get("timeout", 600)), orca_env(S))
                    for kv in exports:
                        k, val = kv.split("=", 1)
                        st["vars"][k.strip()] = val.strip()
                    step["attempts"][-1].update(ended=now(), rc=rc, line=line)
                    step["status"] = "done" if rc == 0 else "script_failed"
                    journal(S, a.pr, step["id"], "", "", f"script exit {rc}: {line}")
                else:
                    start_worker(S, st, step)
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
                    step["status"] = "done" if all(ok for ok, _ in results) else "check_failed"
                    save_chain(S, st)

            stuck = [s for s in group if s["status"] in PAUSED]
            if stuck:
                gate = next((s for s in stuck if s["status"] == "blocked"), None)
                if gate is not None:
                    lines = [f"WAKE gate · {a.pr} · {gate['id']}: " + render(gate["def"].get("title", "the coordinator decides"), step_vars(st, gate))]
                    for c in gate["def"].get("show") or []:
                        lines.append(run_line(render(c, step_vars(st, gate), shell=True), st["def_dir"], 120, orca_env(S))[1])
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


def cmd_worker(S: State, a: argparse.Namespace) -> int:
    spec = Path(a.spec_file).read_text() if a.spec_file else a.spec
    if not spec:
        die("give --spec or --spec-file", 2)
    args = ["orchestration", "worker-start", "--spec", spec, "--task-title", a.label, "--worktree", a.worktree,
            "--agent", a.agent, "--timeout-ms", str(START_TIMEOUT_MS)]
    if a.model:
        args += ["--model", a.model] + (["--effort", a.effort] if a.effort else [])
    d, raw = orca(S, args, timeout=START_TIMEOUT_MS / 1000 + 120)
    res = (d or {}).get("result") or {}
    if orca_error(d) or res.get("state") != "ready":
        out = S / f"start-adhoc-{safe(a.label)}.json"
        out.write_text(json.dumps(d, indent=1) if d else raw)
        die(f"NOT OK {a.label}: the worker did not start; receipt: {out}")
    write_json(S / "dispatches" / f"{safe(res['dispatchId'])}.json",
               {"dispatch": res["dispatchId"], "task": res.get("taskId", ""), "pr": a.pr or "", "step": "",
                "title": a.label, "started": now(), "adhoc": True,
                "who": " ".join(x for x in (a.agent, a.model, a.effort if a.model else "") if x)})
    journal(S, a.pr or "", "adhoc", res.get("taskId", ""), res["dispatchId"], f"started ad hoc worker {a.label} ({a.agent} {a.model or ''})")
    refresh_progress(S)
    print(f"OK {a.label}: task {res.get('taskId')} · dispatch {res['dispatchId']} · rings when done")
    return 0


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
           "picked_up": True, "url": "", "steps": []}
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
    plan = read_json(S / "plan.json") or {}
    planned = [p for p in plan.get("prs") or [] if isinstance(p, dict)]
    chains = {}
    for p in (S / "chains").glob("*/state.json"):
        st = read_json(p)
        if st and st.get("pr"):
            chains[st["pr"].lower()] = st
    rows = [pr_row(S, item, chains.pop(str(item.get("id") or "").lower(), None)) for item in planned]
    for st in sorted(chains.values(), key=lambda c: c.get("created") or ""):
        rows.append(pr_row(S, {"id": st["pr"], "part": "not in the plan" if planned else ""}, st))

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
        tmp = page.with_name(f".{page.name}.{os.getpid()}.tmp")
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
    try:
        a, extra = ap.parse_known_args()
    except SystemExit:
        return 2
    if not a.cmd:
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
            "plan": cmd_plan}[a.cmd](S, a)


if __name__ == "__main__":
    sys.exit(main())
