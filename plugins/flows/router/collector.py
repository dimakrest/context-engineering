#!/usr/bin/env python3
"""flows collector: every settled dispatch's logs, gathered whole and tied to the agent session file that ran it.

Why: the report (M4) has to say where a run's time and tokens went without anyone reading a transcript. Orca's
worker-read archive is bounded (older messages are clipped), so it is kept, but the agent's own session file is
the full log: the collector finds it, copies it, and sums its token usage.

Usage:
  collector.py collect --state <dir> (--dispatch <id> | --pr <pr> | --all) [--force] [--dry-run]
                       [--claude-projects <dir>] [--codex-sessions <dir>] [--orca <cmd>] [--wait-settled <s>]
    --force         collect again a dispatch already collected (its directory and its index row are replaced)
    --dry-run       print the session match verdict per dispatch and the counts; copies, writes and asks nothing
    --wait-settled  wait up to <s> seconds for the chain to record the attempt's end and checks (the daemon's hook)

What it writes, under <state>/logs/ only (never into a repository):
  <pr>/<step>/<dispatch>/   (an ad hoc worker's step is "_adhoc", a dispatch with no PR is under "_none")
    meta.json        dispatch, task, pr, step, attempt n, agent/model/effort as started (and as effective when the
                     start receipt differed), started, ended, outcome, flow_version, cause, worktree, head_before,
                     head_after, the check lines, the release result, and where every file below came from
    orca-read.json   every `orca orchestration worker-read` page, cursor followed until none or a page repeats,
                     contentComplete and clipping kept; {"status": "not available"} when the archive never answered
    events/          the dispatch's events/*.json and its liveness file, copied
    session.jsonl    the matched agent session file (Claude Code or Codex), copied byte for byte; absent when no
                     single file matches, and meta.json says why: "no candidate", "ambiguous: <n> candidates",
                     "provider not supported", "worktree unknown"
    tokens.json      summed from session.jsonl: input, output, cache_creation, cache_read, by model; turns (one per
                     model response: Claude lines that share a message id are one response); first and last
                     timestamp; "not recorded" for a field the source never reports (Codex: cache_creation, and
                     everything when it wrote no token_count line). Codex counts cached input inside input.
  index.jsonl      one row per dispatch: {dispatch, task, pr, step, n, agent, model, effort, started, ended,
                   outcome, cause, flow_version, duration_s, tokens, session {provider, path, match, candidates},
                   head_before, head_after, checks_ok, orca {contentComplete, clipping}}
  A dispatch already collected (its meta.json exists) is skipped unless --force, so collecting is idempotent.

Session matching. Candidates are session files whose cwd is the dispatch's worktree:
  Claude Code  <claude-projects>/<cwd with "/" replaced by "-">/<session>.jsonl, and the cwd inside the file agrees.
               A file whose lines are all sidechain lines is not a candidate (a sidechain belongs to its parent file,
               where it is kept); subagent files in a session's own subdirectory are not candidates either.
  Codex        <codex-sessions>/YYYY/MM/DD/rollout-*.jsonl whose first line's session_meta.payload.cwd is the worktree.
  and whose first..last timestamps overlap [started - 2 min, ended + 2 min]. One candidate: "unique". Several: the
  one whose first timestamp falls within 3 minutes after the start, if exactly one does ("unique"); otherwise
  "ambiguous", the candidate paths are recorded and nothing is copied. The collector never guesses.

Privacy: the copies stay in the state directory. index.jsonl and meta.json hold ids, paths, timestamps, token counts
and the router's own check lines, never transcript text (nor a worker_done's subject or body).

Errors: a dispatch that cannot be collected prints "collector: <dispatch>: <why>" on stderr, journals that line in
<state>/journal.md, and the exit code is 1; the other dispatches are still collected.

Env (the flags win): FLOWS_CLAUDE_PROJECTS (default ~/.claude/projects), FLOWS_CODEX_SESSIONS (default
~/.codex/sessions), ORCA_CLI_COMMAND (default orca), FLOWS_ORCA_RETRY_S (the pause between two worker-read tries when
the archive answers archive_not_ready; default 2).
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SLACK_S = 120          # the window around a dispatch a session file must overlap
NEAR_START_S = 180     # several candidates: the one that began this soon after the start
NOT_READY = "archive_not_ready"
READ_TRIES = 4         # the first worker-read page, and 3 retries while the archive is not ready
RETRY_S = float(os.environ.get("FLOWS_ORCA_RETRY_S", "2"))
MAX_PAGES = 500
NR = "not recorded"
VAR_RE = re.compile(r"(?<!\$)\{([A-Z][A-Z0-9_]*)\}")


class CollectError(Exception):
    """Why one dispatch could not be collected, in one line."""


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts_epoch(ts: object) -> "float | None":
    """An ISO timestamp (Z or an offset, with or without fractions) as epoch seconds."""
    if not isinstance(ts, str) or not ts:
        return None
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:?\d\d)?$", ts.strip())
    if not m:
        return None
    zone = "+00:00" if m.group(3) in (None, "Z") else m.group(3)
    try:   # fromisoformat before Python 3.11 takes neither "Z" nor a fraction of other than 3 or 6 digits
        d = datetime.datetime.fromisoformat(m.group(1) + "." + (m.group(2) or "0")[:6].ljust(6, "0") + zone)
    except ValueError:
        return None
    return d.timestamp()


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def oneline(text: object, limit: int = 300) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def read_json(path: Path) -> "dict | None":
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=1) + "\n")
    os.replace(tmp, path)


def journal(state: Path, pr: str, step: str, task: str, dispatch: str, text: str) -> None:
    """The router's journal line format (router.py journal)."""
    with open(state / "journal.md", "a") as f:
        f.write(f"{now()} | {pr or '-'} | {step or '-'} | {task or '-'} | {dispatch or '-'} | {oneline(text, 400)}\n")


# ---------------------------------------------------------------- what the router recorded about a dispatch

def render(text: str, variables: "dict[str, str]") -> "str | None":
    """{NAME} filled from the chain's variables; None when one has no value."""
    missing = []

    def sub(m: "re.Match[str]") -> str:
        if m.group(1) not in variables:
            missing.append(m.group(1))
            return ""
        return str(variables[m.group(1)])

    out = VAR_RE.sub(sub, text)
    return None if missing else out


def worktree_of(selector: "str | None") -> "str | None":
    """An Orca --worktree selector names a directory only as path:<dir>; "current" and the others do not."""
    if selector and selector.startswith("path:") and selector[5:].strip():
        return selector[5:].strip()
    return None


def find_attempt(state: Path, dispatch: str, pr_hint: str) -> "tuple[dict, dict, dict] | None":
    """(chain state, step, attempt) of the worker step that started this dispatch."""
    paths = sorted((state / "chains").glob("*/state.json"))
    if pr_hint:
        paths.sort(key=lambda p: p.parent.name != safe(pr_hint))
    for p in paths:
        st = read_json(p) or {}
        for step in st.get("steps") or []:
            for att in step.get("attempts") or []:
                if att.get("dispatch") == dispatch:
                    return st, step, att
    return None


def worker_done_event(state: Path, dispatch: str) -> dict:
    files = sorted((state / "events" / safe(dispatch)).glob("*-worker_done-*.json"))
    return (read_json(files[-1]) or {}) if files else {}


def describe(state: Path, dispatch: str) -> dict:
    """Everything the router's files say about one dispatch. No message text is taken."""
    reg = read_json(state / "dispatches" / f"{safe(dispatch)}.json") or {}
    found = find_attempt(state, dispatch, reg.get("pr", ""))
    ev = worker_done_event(state, dispatch)
    info: dict = {"dispatch": dispatch, "task": reg.get("task", ""), "pr": reg.get("pr", ""), "step": reg.get("step", ""),
                  "title": reg.get("title", ""), "adhoc": bool(reg.get("adhoc")), "n": None, "agent": None, "model": None,
                  "effort": None, "effective": None, "started": reg.get("started"), "ended": None,
                  "outcome": reg.get("outcome"), "flow_version": None, "cause": None, "worktree": None,
                  "worktree_source": "unknown", "head_before": None, "head_after": None, "checks": [],
                  "release": ev.get("release"), "settled": bool(reg.get("settled")), "chain_settled": None,
                  "known": bool(reg) or found is not None}
    if found is not None:
        st, step, att = found
        d = dict(step.get("def") or {}, **(step.get("override") or {}))
        v = dict(st.get("vars") or {})
        v.update(STEP=step.get("id", ""), ATTEMPT=str(att.get("n", 1)), HEAD_BEFORE=att.get("head_before", ""))
        info.update(pr=st.get("pr", info["pr"]), step=step.get("id", info["step"]), n=att.get("n"),
                    task=att.get("task") or info["task"], started=att.get("started") or info["started"],
                    ended=att.get("ended"), outcome=att.get("outcome") or info["outcome"],
                    flow_version=att.get("flow_version"), cause=att.get("cause"), head_before=att.get("head_before"),
                    head_after=att.get("head_after"), checks=list(att.get("checks") or []),
                    release=att.get("release") or info["release"], chain_settled=bool(att.get("ended")))
        for k in ("agent", "model", "effort"):   # recorded at the start since M3; rendered from the step before that
            info[k] = att.get(k) or (render(str(d[k]), v) if d.get(k) else None)
        info["effective"] = att.get("effective") or att.get("launch_differs")
        if att.get("worktree"):
            info.update(worktree=worktree_of(att["worktree"]), worktree_source=f"attempt: {att['worktree']}")
        else:
            sel = render(str(d.get("worktree", "current")), v)
            info.update(worktree=worktree_of(sel), worktree_source=f"step: {sel}" if sel else "step: a variable has no value")
        info["settled"] = info["settled"] or bool(att.get("ended"))
    else:
        if reg.get("agent") or reg.get("who"):
            who = (reg.get("who") or "").split()
            info.update(agent=reg.get("agent") or (who[0] if who else None),
                        model=reg.get("model") or (who[1] if len(who) > 1 else None),
                        effort=reg.get("effort") or (who[2] if len(who) > 2 else None))
        info["worktree_source"] = "registry: no worktree recorded"   # a worker started before the router recorded it
        if reg.get("worktree"):
            info.update(worktree=worktree_of(reg["worktree"]), worktree_source=f"registry: {reg['worktree']}")
    if not info["ended"]:
        msg = ev.get("message") or {}
        info["ended"] = reg.get("settled") or msg.get("created_at") or ev.get("received")
    if not info["outcome"]:
        info["outcome"] = (ev.get("payload") or {}).get("outcome")
    return info


def all_dispatches(state: Path) -> "list[str]":
    seen: "dict[str, None]" = {}
    for p in sorted((state / "dispatches").glob("*.json")):
        d = (read_json(p) or {}).get("dispatch")
        if d:
            seen[d] = None
    for p in sorted((state / "chains").glob("*/state.json")):
        for step in (read_json(p) or {}).get("steps") or []:
            for att in step.get("attempts") or []:
                if att.get("dispatch"):
                    seen[att["dispatch"]] = None
    return list(seen)


def logs_dir(state: Path, info: dict) -> Path:
    step = "_adhoc" if info["adhoc"] else (info["step"] or "_none")
    return state / "logs" / safe(info["pr"] or "_none") / safe(step) / safe(info["dispatch"])


# ---------------------------------------------------------------- session files

def jsonl_head(path: Path, limit: int = 400):
    with open(path, "rb") as f:
        for i, raw in enumerate(f):
            if i >= limit:
                return
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if isinstance(d, dict):
                yield d


def last_timestamp(path: Path) -> "str | None":
    """The last line's timestamp, read from the end; a line longer than the tail falls back to a full read."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        f.seek(max(0, size - 262144))
        tail = f.read().splitlines()
    for raw in reversed(tail[1:] if size > 262144 else tail):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("timestamp"):
            return d["timestamp"]
    last = None
    with open(path, "rb") as f:
        for raw in f:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("timestamp"):
                last = d["timestamp"]
    return last


def same_dir(a: "str | None", b: "str | None") -> bool:
    if not a or not b:
        return False
    a, b = a.rstrip("/") or "/", b.rstrip("/") or "/"
    return a == b or os.path.realpath(a) == os.path.realpath(b)


_cache: "dict[str, dict]" = {}


def claude_file(path: Path) -> dict:
    """cwd, first and last timestamp, and whether any line is the main conversation (not a sidechain)."""
    key = str(path)
    if key not in _cache:
        cwd, first, main = None, None, False
        for d in jsonl_head(path):
            cwd = cwd or d.get("cwd")
            first = first or d.get("timestamp")
            if d.get("type") in ("user", "assistant") and not d.get("isSidechain"):
                main = True
            if cwd and first and main:
                break
        _cache[key] = {"path": str(path), "cwd": cwd, "first": first, "last": last_timestamp(path) if first else None, "main": main}
    return _cache[key]


def codex_file(path: Path) -> dict:
    key = str(path)
    if key not in _cache:
        cwd, first = None, None
        for d in jsonl_head(path, 1):
            if d.get("type") == "session_meta":
                payload = d.get("payload") or {}
                cwd, first = payload.get("cwd"), payload.get("timestamp") or d.get("timestamp")
        _cache[key] = {"path": str(path), "cwd": cwd, "first": first, "last": last_timestamp(path) if first else None, "main": True}
    return _cache[key]


def root_dir(path: Path, what: str) -> "Path | None":
    if not path.exists():
        return None
    if not path.is_dir():
        raise CollectError(f"the {what} root is not a directory: {path}")
    return path


def claude_candidates(root: Path, worktree: str) -> "list[dict]":
    if root_dir(root, "Claude projects") is None:
        return []
    names = dict.fromkeys([worktree.replace("/", "-"), re.sub(r"[^A-Za-z0-9]", "-", worktree)])
    out = []
    for name in names:
        for p in sorted((root / name).glob("*.jsonl")):
            f = claude_file(p)
            if f["main"] and same_dir(f["cwd"], worktree):
                out.append(f)
    return out


def codex_candidates(root: Path, worktree: str, lo: float, hi: float) -> "list[dict]":
    if root_dir(root, "Codex sessions") is None:
        return []
    days, t = [], lo - 86400
    while t <= hi + 86400:   # the directories are dated in local time; a day either side covers any offset
        day = datetime.datetime.fromtimestamp(t)
        days.append(root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}")
        t += 86400
    out = []
    for d in dict.fromkeys(days):
        for p in sorted(d.glob("rollout-*.jsonl")):
            f = codex_file(p)
            if same_dir(f["cwd"], worktree):
                out.append(f)
    return out


def provider_of(info: dict, orca_provider: "str | None") -> "str | None":
    agent = (info.get("agent") or orca_provider or "").lower()
    return agent if agent in ("claude", "codex") else (agent or None)


def match_session(info: dict, provider: "str | None", claude_root: Path, codex_root: Path) -> dict:
    """{provider, path, match: unique | ambiguous | none, candidates, why}. Never a guess."""
    res: dict = {"provider": provider, "path": None, "match": "none", "candidates": 0, "why": ""}
    if provider not in ("claude", "codex"):
        res["why"] = "provider not supported" + (f": {provider}" if provider else ": unknown")
        return res
    if not info.get("worktree"):
        res["why"] = f"worktree unknown ({info.get('worktree_source')})"
        return res
    start, end = ts_epoch(info.get("started")), ts_epoch(info.get("ended"))
    if start is None or end is None:
        res["why"] = "the dispatch window is unknown: " + ("no start" if start is None else "no end")
        return res
    lo, hi = start - SLACK_S, end + SLACK_S
    files = claude_candidates(claude_root, info["worktree"]) if provider == "claude" else codex_candidates(codex_root, info["worktree"], lo, hi)
    overlap = []
    for f in files:
        first, last = ts_epoch(f["first"]), ts_epoch(f["last"])
        if first is not None and last is not None and first <= hi and last >= lo:
            overlap.append(f)
    res["candidates"] = len(overlap)
    if not overlap:
        res["why"] = "no candidate"
        return res
    if len(overlap) > 1:
        near = [f for f in overlap if start - 1 <= (ts_epoch(f["first"]) or 0) <= start + NEAR_START_S]
        if len(near) != 1:
            res.update(match="ambiguous", why=f"ambiguous: {len(overlap)} candidates", paths=[f["path"] for f in overlap])
            return res
        overlap = near
        res["why"] = f"{res['candidates']} candidates; the only one that began within {NEAR_START_S // 60} min of the start"
    res.update(match="unique", path=overlap[0]["path"])
    return res


# ---------------------------------------------------------------- tokens

def claude_tokens(path: Path) -> dict:
    """Claude Code writes one line per content block; the lines of one response share message.id and its usage."""
    resp: "dict[str, tuple[str, dict, bool]]" = {}
    first = last = None
    for i, raw in enumerate(open(path, "rb")):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        if d.get("timestamp"):
            first = first or d["timestamp"]
            last = d["timestamp"]
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        if d.get("type") == "assistant" and isinstance(msg.get("usage"), dict):
            resp[str(msg.get("id") or d.get("uuid") or i)] = (str(msg.get("model") or "unknown"), msg["usage"], bool(d.get("isSidechain")))
    fields = {"input": "input_tokens", "output": "output_tokens", "cache_creation": "cache_creation_input_tokens",
              "cache_read": "cache_read_input_tokens"}
    total: dict = {k: NR for k in fields}
    by_model: dict = {}
    for model, usage, _side in resp.values():
        m = by_model.setdefault(model, dict({k: NR for k in fields}, turns=0))
        m["turns"] += 1
        for k, src in fields.items():
            if isinstance(usage.get(src), (int, float)):
                m[k] = (0 if m[k] == NR else m[k]) + int(usage[src])
                total[k] = (0 if total[k] == NR else total[k]) + int(usage[src])
    return dict(provider="claude", **total, turns=len(resp), sidechain_turns=sum(1 for r in resp.values() if r[2]),
                by_model=by_model, first=first or NR, last=last or NR)


def codex_tokens(path: Path) -> dict:
    """token_count events carry a running total; each turn's growth is counted to the model of that turn."""
    first = last = None
    model, turns, prev = "unknown", 0, None
    by_model: dict = {}
    fields = {"input": "input_tokens", "output": "output_tokens", "cache_read": "cached_input_tokens"}
    for raw in open(path, "rb"):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        if d.get("timestamp"):
            first = first or d["timestamp"]
            last = d["timestamp"]
        p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        if d.get("type") == "turn_context":
            model = str(p.get("model") or model)
            turns += 1
            by_model.setdefault(model, {"input": NR, "output": NR, "cache_creation": NR, "cache_read": NR, "turns": 0})["turns"] += 1
        tot = ((p.get("info") or {}).get("total_token_usage") if d.get("type") == "event_msg" and p.get("type") == "token_count"
               and isinstance(p.get("info"), dict) else None)
        if isinstance(tot, dict):
            m = by_model.setdefault(model, {"input": NR, "output": NR, "cache_creation": NR, "cache_read": NR, "turns": 0})
            for k, src in fields.items():
                if isinstance(tot.get(src), (int, float)):
                    grew = int(tot[src]) - int((prev or {}).get(src) or 0)
                    m[k] = (0 if m[k] == NR else m[k]) + grew
            prev = tot
    total = {k: (int(prev[src]) if prev and isinstance(prev.get(src), (int, float)) else NR) for k, src in fields.items()}
    return dict(provider="codex", input=total["input"], output=total["output"], cache_creation=NR, cache_read=total["cache_read"],
                turns=turns if turns else NR, by_model=by_model, first=first or NR, last=last or NR)


# ---------------------------------------------------------------- orca worker-read

def orca_env(state: Path) -> "dict[str, str]":
    """Speak to Orca as the coordinator's terminal, as the router's daemons do."""
    env = dict(os.environ)
    run = read_json(state / "run.json") or {}
    if run.get("terminal"):
        env["ORCA_TERMINAL_HANDLE"] = run["terminal"]
    if run.get("pane"):
        env["ORCA_PANE_KEY"] = run["pane"]
    return env


def orca_call(orca: str, args: "list[str]", env: "dict[str, str]") -> "tuple[dict | None, str]":
    try:
        p = subprocess.run([orca] + args + ["--json"], capture_output=True, text=True, errors="replace", timeout=180, env=env)
    except subprocess.TimeoutExpired:
        return None, "timed out"
    except OSError as e:
        return None, f"cannot run {orca}: {e}"
    for text in (p.stdout, p.stderr):
        i = text.find("{")
        if i >= 0:
            try:
                return json.JSONDecoder().raw_decode(text[i:])[0], ""
            except ValueError:
                pass
    return None, "unreadable"


def orca_code(d: "dict | None", why: str) -> str:
    if d is None:
        return why or "unreadable"
    if d.get("ok") is False or d.get("error"):
        return str((d.get("error") or {}).get("code") or "error")
    return ""


def orca_read(orca: str, env: "dict[str, str]", dispatch: str) -> dict:
    """Every page of the bounded archive, in order. A first page that says archive_not_ready (or cannot be read) is
    asked again up to 3 times; then the archive is recorded as not available, never as an empty transcript."""
    base = ["orchestration", "worker-read", "--dispatch", dispatch, "--source", "transcript", "--limit", "50"]
    out: dict = {"dispatch": dispatch, "status": "ok", "pages": []}
    cursor, seen_cursors, seen_ids = None, set(), set()
    for page_no in range(MAX_PAGES):
        tries = READ_TRIES if page_no == 0 else 1
        for t in range(tries):
            d, why = orca_call(orca, base + (["--cursor", cursor] if cursor else []), env)
            code = orca_code(d, why)
            if not code or code not in (NOT_READY, "unreadable", "timed out") or t == tries - 1:
                break
            time.sleep(RETRY_S)
        if code:
            if page_no == 0:
                out.update(status="not available", error=code, tries=tries)
            else:
                out.update(error=f"page {page_no + 1}: {code}")
            break
        res = (d or {}).get("result") or {}
        ids = tuple(m.get("id") for m in ((res.get("transcript") or {}).get("messages") or []) if isinstance(m, dict))
        if page_no and ids and ids in seen_ids:
            out["stopped"] = "a page repeated"
            break
        seen_ids.add(ids)
        out["pages"].append(res)
        nxt = res.get("cursor")
        if not nxt:
            break
        if nxt in seen_cursors or nxt == cursor:
            out["stopped"] = "a cursor repeated"
            break
        seen_cursors.add(nxt)
        cursor = nxt
    pages = out["pages"]
    if pages:
        out.update(provider=pages[0].get("provider"), messages=sum(len((p.get("transcript") or {}).get("messages") or []) for p in pages),
                   contentComplete=pages[-1].get("contentComplete"),
                   clipping=list(dict.fromkeys(str(c) for p in pages for c in (p.get("clipping") or []))))
    return out


# ---------------------------------------------------------------- one dispatch

def index_row(meta: dict, tokens: "dict | None") -> dict:
    s, e = ts_epoch(meta.get("started")), ts_epoch(meta.get("ended"))
    sess = meta.get("session") or {}
    tok = "not recorded"
    if tokens:
        tok = {k: tokens.get(k) for k in ("input", "output", "cache_creation", "cache_read", "turns")}
        tok["by_model"] = tokens.get("by_model") or {}
    checks = meta.get("checks") or []
    orca = meta.get("orca") or {}
    return {"dispatch": meta["dispatch"], "task": meta.get("task"), "pr": meta.get("pr"), "step": meta.get("step"),
            "n": meta.get("n"), "agent": meta.get("agent"), "model": meta.get("model"), "effort": meta.get("effort"),
            "started": meta.get("started"), "ended": meta.get("ended"), "outcome": meta.get("outcome"),
            "cause": meta.get("cause"), "flow_version": meta.get("flow_version"),
            "duration_s": int(e - s) if s is not None and e is not None else None, "tokens": tok,
            "session": {"provider": sess.get("provider"), "path": sess.get("path"), "match": sess.get("match"),
                        "candidates": sess.get("candidates")},
            "head_before": meta.get("head_before"), "head_after": meta.get("head_after"),
            "checks_ok": all(str(c).startswith("OK") for c in checks) if checks else meta.get("outcome") == "succeeded",
            "orca": {"contentComplete": orca.get("contentComplete"), "clipping": orca.get("clipping")}}


class Collector:
    def __init__(self, state: Path, claude_root: Path, codex_root: Path, orca: str, force: bool = False, dry_run: bool = False) -> None:
        self.state, self.claude_root, self.codex_root, self.orca = state, claude_root, codex_root, orca
        self.force, self.dry_run = force, dry_run
        self.logs = state / "logs"

    def wait_settled(self, dispatch: str, seconds: float) -> dict:
        """The daemon releases a worker before its chain records the end and runs the checks: wait for those."""
        end = time.time() + seconds
        while True:
            info = describe(self.state, dispatch)
            if info["chain_settled"] is not False or time.time() >= end:
                return info
            time.sleep(1.0)

    def collected(self, info: dict) -> bool:
        return (logs_dir(self.state, info) / "meta.json").exists()

    def dry(self, info: dict) -> dict:
        provider = provider_of(info, None)
        return match_session(info, provider, self.claude_root, self.codex_root)

    def collect(self, info: dict) -> str:
        """Collect one dispatch. Returns what happened in a few words; raises CollectError."""
        d = info["dispatch"]
        final = logs_dir(self.state, info)
        lockdir = self.logs / ".locks" / safe(d)
        lockdir.parent.mkdir(parents=True, exist_ok=True)
        try:
            lockdir.mkdir()
        except FileExistsError:
            if time.time() - lockdir.stat().st_mtime < 7200:
                return "being collected by another collector"
            lockdir.rmdir()   # left by a collector that died
            lockdir.mkdir()
        try:
            if (final / "meta.json").exists() and not self.force:
                self.index(read_json(final / "meta.json") or {}, read_json(final / "tokens.json"))   # a row a crash lost
                return "already collected"
            return self._collect(info, final)
        finally:
            lockdir.rmdir()

    def _collect(self, info: dict, final: Path) -> str:
        d = info["dispatch"]
        tmp = final.with_name(f".{final.name}.{os.getpid()}.tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        (tmp / "events").mkdir(parents=True)
        files: dict = {}

        read = orca_read(self.orca, orca_env(self.state), d)
        write_json(tmp / "orca-read.json", read)
        files["orca-read.json"] = {"source": f"{self.orca} orchestration worker-read --source transcript", "status": read["status"],
                                   "pages": len(read["pages"]), "messages": read.get("messages", 0)}
        if read["status"] != "ok":
            files["orca-read.json"]["note"] = f"orca-read: not available ({read.get('error')})"

        evdir = self.state / "events" / safe(d)
        n = 0
        for p in sorted(evdir.glob("*.json")) if evdir.is_dir() else []:
            shutil.copyfile(p, tmp / "events" / p.name)
            n += 1
        live = self.state / "liveness" / safe(d)
        if live.is_file():
            shutil.copyfile(live, tmp / "events" / "liveness")
        files["events/"] = {"source": str(evdir), "files": n, "liveness": live.is_file()}

        provider = provider_of(info, read.get("provider"))
        sess = match_session(info, provider, self.claude_root, self.codex_root)
        tokens = None
        if sess["match"] == "unique":
            src = Path(sess["path"])
            shutil.copyfile(src, tmp / "session.jsonl")
            sub = src.with_suffix("") / "subagents"
            if provider == "claude" and sub.is_dir():
                sess["subagent_files_not_copied"] = len(list(sub.glob("*.jsonl")))
            tokens = claude_tokens(tmp / "session.jsonl") if provider == "claude" else codex_tokens(tmp / "session.jsonl")
            write_json(tmp / "tokens.json", tokens)
            files["session.jsonl"] = {"source": provider, "path": sess["path"], "match": "unique", "candidates": sess["candidates"]}
            files["tokens.json"] = {"source": "session.jsonl"}
        else:
            files["session.jsonl"] = {"source": provider, "path": None, "match": sess["match"], "candidates": sess["candidates"],
                                      "why": sess["why"]}

        meta = {k: info[k] for k in ("dispatch", "task", "pr", "step", "title", "adhoc", "n", "agent", "model", "effort",
                                     "effective", "started", "ended", "outcome", "flow_version", "cause", "worktree",
                                     "worktree_source", "head_before", "head_after", "checks", "release")}
        meta["settled_in_chain"] = info["chain_settled"]
        meta["session"] = sess
        meta["orca"] = {"status": read["status"], "provider": read.get("provider"), "contentComplete": read.get("contentComplete"),
                        "clipping": read.get("clipping")}
        meta["files"] = files
        write_json(tmp / "meta.json", meta)
        if final.exists():
            shutil.rmtree(final)
        os.replace(tmp, final)
        self.index(meta, tokens)
        return f"session {sess['match']}" + (f" ({sess['why']})" if sess["why"] else "")

    def index(self, meta: dict, tokens: "dict | None") -> None:
        if not meta.get("dispatch"):
            return
        row = json.dumps(index_row(meta, tokens))
        path = self.logs / "index.jsonl"
        with open(self.logs / ".index.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            rows = path.read_text().splitlines() if path.exists() else []
            at = [i for i, r in enumerate(rows) if json.loads(r).get("dispatch") == meta["dispatch"]]
            if at and rows[at[0]] == row:
                return
            if at:
                rows[at[0]] = row
                tmp = path.with_name(f".index.{os.getpid()}.tmp")
                tmp.write_text("\n".join(rows) + "\n")
                os.replace(tmp, path)
            else:
                with open(path, "a") as f:
                    f.write(row + "\n")


# ---------------------------------------------------------------- command line

def main(argv: "list[str] | None" = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0 if argv else 2
    ap = argparse.ArgumentParser(prog="collector.py", add_help=False)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("collect", add_help=False)
    p.add_argument("--state", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dispatch")
    g.add_argument("--pr")
    g.add_argument("--all", action="store_true")
    p.add_argument("--claude-projects", default=os.environ.get("FLOWS_CLAUDE_PROJECTS") or str(Path.home() / ".claude" / "projects"))
    p.add_argument("--codex-sessions", default=os.environ.get("FLOWS_CODEX_SESSIONS") or str(Path.home() / ".codex" / "sessions"))
    p.add_argument("--orca", default=os.environ.get("ORCA_CLI_COMMAND", "orca"))
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--wait-settled", type=float, default=0)
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    if a.cmd != "collect":
        print(__doc__.strip())
        return 2
    state = Path(a.state)
    if not state.is_dir():
        print(f"collector: no state directory: {state}", file=sys.stderr)
        return 1
    c = Collector(state, Path(a.claude_projects).expanduser(), Path(a.codex_sessions).expanduser(), a.orca, a.force, a.dry_run)

    if a.dispatch:
        targets = [a.dispatch]
    else:
        targets = all_dispatches(state)
    counts = {"collected": 0, "skipped": 0, "failed": 0, "not settled": 0}
    verdicts = {"unique": 0, "ambiguous": 0, "none": 0}
    for d in targets:
        info = c.wait_settled(d, a.wait_settled) if a.dispatch and a.wait_settled and not a.dry_run else describe(state, d)
        if a.pr and info["pr"] != a.pr:
            continue
        if not info["known"]:
            print(f"collector: {d}: the router has no record of this dispatch", file=sys.stderr)
            counts["failed"] += 1
            continue
        if not info["settled"]:
            print(f"skip {d}: not settled")
            counts["not settled"] += 1
            continue
        if a.dry_run:
            try:
                v = c.dry(info)
            except CollectError as e:
                print(f"collector: {d}: {e}", file=sys.stderr)
                counts["failed"] += 1
                continue
            verdicts[v["match"]] += 1
            print(f"{d} · {info['pr'] or '-'} {info['step'] or ('adhoc' if info['adhoc'] else '-')} · {v['provider'] or '-'} · "
                  f"{v['match']}" + (f" ({v['why']})" if v["why"] else ""))
            continue
        try:
            what = c.collect(info)
        except (CollectError, OSError, ValueError) as e:
            why = f"collector: {d}: {oneline(e, 300)}"
            print(why, file=sys.stderr)
            journal(state, info["pr"], info["step"], info["task"], d, why)
            counts["failed"] += 1
            continue
        if what in ("already collected", "being collected by another collector"):
            counts["skipped"] += 1
            print(f"skip {d}: {what}")
        else:
            counts["collected"] += 1
            print(f"collected {d} · {info['pr'] or '-'} {info['step'] or '-'} · {what}")
    if a.dry_run:
        print(f"dry run: {sum(verdicts.values())} settled dispatches · unique {verdicts['unique']} · ambiguous {verdicts['ambiguous']}"
              f" · none {verdicts['none']} · not settled {counts['not settled']} · failed {counts['failed']} · nothing copied")
    else:
        print(f"{'OK' if not counts['failed'] else 'NOT OK'} collected {counts['collected']} · skipped {counts['skipped']} · "
              f"not settled {counts['not settled']} · failed {counts['failed']}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
