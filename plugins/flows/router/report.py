#!/usr/bin/env python3
"""flows report: one self-contained HTML page per run, built from the router's records alone.

Why: the run is over and the planner of the next one asks where the time, the tokens and the interruptions went.
Every number on the page comes from a record named next to it; a number whose record is missing prints "not
recorded". Nothing is interpolated or estimated, and no transcript text is shown. Without journal.md, what only it
records is "not recorded" (gate waits, pauses, the rest, open waits, the rings but the silent ones, NOT OK checks,
questions and their answers); an empty journal.md is a real 0.

Usage:
  report.py --state <dir> --out <file.html> [--metrics <file.json>] [--prices <file.json>] [--pr <pr>]
  router.py report [--out <file>] [--pr <pr>] [--metrics <file>] [--prices <file>]   (default <state>/report.html)
    --metrics  the same numbers as JSON
    --prices   per-model prices per million tokens: {"<model>": {"input", "output", "cache_creation", "cache_read"}};
               without it every cost is "not priced"
    --pr       the page shows that PR's section only (the metrics cover the whole run)

What it reads (it writes only the files named on its command line, and never calls Orca):
  run.json, flow.json (or plan.json), chains/<pr>/state.json, dispatches/<dispatch>.json, journal.md,
  the names of events/<dispatch>/*-question-<id>.json and of wake/[seen/]*-silent.txt (never their text),
  logs/<pr>/<step>/<dispatch>/meta.json and tokens.json. Never session.jsonl nor session.subagents/.

The questions, and the record behind each answer:
  a. time per PR      wall clock: state.json created..ended (a chain not done: "not recorded"); worker time: the sum
                      of started..ended over the attempts that have a dispatch, and the time at least one of them
                      ran ("covered": a group's workers run at once, so the sum can pass the wall clock). Gate waits:
                      a journal "paused: blocked" line to the PR's next "coordinator:" line; pauses: any other
                      "paused:" line to the PR's next "coordinator:" line (a wait that another pause or "chain
                      complete" ends first has no recorded end and is counted as open). The split is a partition of
                      the wall clock, each second in one part, by priority: covered, then at gates (gate waits less
                      covered), then paused (pauses less both), then the rest (starts, checks, scripts). So no part
                      is negative and the four add up to the wall clock.
                      Ad hoc workers: dispatches/<dispatch>.json started..settled, shown apart.
  b. steps            per step id across PRs: the duration of a PR's step is the sum of its attempts' started..ended;
                      median and max; attempts by state.json's cause (an attempt without one: "first" when n is 1,
                      else "not recorded"); journal "check: NOT OK" lines per step.
  c. interruptions    rings per kind from journal lines: "paused: blocked" gate, "paused: failed" failed,
                      "paused: check_failed" check, "paused: start_*" start, "paused: script_failed" script, any other
                      "paused:" runner, "question:" question, "<message type>:" (escalation, merge_ready, ...);
                      silent rings from the wake/ file names; "collector: <dispatch>: ..." lines are counted under
                      collector, and those saying "locked by another collector" under locked. Questions per role (the
                      asking step's spec); time to answer: the question's created time (its event file's name) to
                      the journal's "coordinator: replied to <message id>" line.
  d. tokens           tokens.json per dispatch: by the model the step asked for ("requested -> effective" when Orca
                      launched another), with the subagents' share where tokens.json has the object; by the model
                      the session reported (by_model); cost from --prices. A Codex session counts cached input inside
                      input, so its uncached input is input - cache_read. A field no dispatch recorded is "not
                      recorded"; a sum covers the dispatches that recorded the field.
  e. review yield     "FINDINGS: <n>" and the word after "VERDICT:" in the attempts' summaries (state.json) of the
                      review and triage steps; only that number and that word are taken.
  f. flow history     flow.json's history rows, and each chain's flow.started_at.
  g. lessons          the slowest step, the most-retried step, the role that asked the most questions, the PR with the
                      longest single wait at a gate, each with its pr, step and dispatch.
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import re
import statistics
import sys
from pathlib import Path

NR = "not recorded"
NP = "not priced"
TOKEN_FIELDS = ("input", "output", "cache_creation", "cache_read", "turns")
PRICE_FIELDS = ("input", "output", "cache_creation", "cache_read")
SUB_FIELDS = ("files", "turns", "input", "output", "cache_creation", "cache_read")
RING_KINDS = ("gate", "question", "failed", "check", "silent", "runner")   # always listed, also at 0
PAUSE_KIND = {"blocked": "gate", "failed": "failed", "check_failed": "check", "start_failed": "start",
              "start_unknown": "start", "script_failed": "script"}
MESSAGE_KINDS = ("escalation", "merge_ready", "handoff", "decision_gate")
OUTSIDE = "-"   # the journal's and the registry's "no PR"


# ---------------------------------------------------------------- reading

def ts_epoch(ts: object) -> "float | None":
    if not isinstance(ts, str) or not ts:
        return None
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:?\d\d)?$", ts.strip())
    if not m:
        return None
    zone = "+00:00" if m.group(2) in (None, "Z") else m.group(2)
    try:
        return datetime.datetime.fromisoformat(m.group(1) + zone).timestamp()
    except ValueError:
        return None


def iso(t: "float | None") -> str:
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t is not None else NR


def read_json(path: Path) -> "dict | None":
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def span(a: object, b: object) -> "int | None":
    s, e = ts_epoch(a), ts_epoch(b)
    return int(e - s) if s is not None and e is not None else None


def journal_lines(state: Path) -> "list[dict]":
    out = []
    try:
        text = (state / "journal.md").read_text(errors="replace")
    except OSError:
        return out
    for raw in text.splitlines():
        parts = raw.split(" | ", 5)
        if len(parts) < 6 or ts_epoch(parts[0].strip()) is None:
            continue
        t, pr, step, task, dispatch, body = (p.strip() for p in parts)
        out.append({"t": t, "e": ts_epoch(t), "pr": pr or OUTSIDE, "step": step, "dispatch": dispatch, "text": body})
    return out


def classify(text: str) -> "tuple[str, str]":
    """(kind, detail) of a journal line. The detail is a router word (a status, a message id), never worker text."""
    if text.startswith("paused: "):
        word = text[8:].split()[0] if text[8:].split() else ""
        return "paused", word
    if text.startswith("coordinator: "):
        m = re.match(r"coordinator: replied to (\S+?):", text)
        return ("replied", m.group(1)) if m else ("coordinator", "")
    if text.startswith("collector: "):
        return ("locked", "") if ": locked by another collector" in text else ("collector", "")
    if text.startswith("check: "):
        return "check", "NOT OK" if text[7:].startswith("NOT OK") else "OK"
    if text.startswith("question: "):
        return "question", ""
    for k in MESSAGE_KINDS:
        if text.startswith(k + ":"):
            return "message", k
    if text == "chain complete":
        return "complete", ""
    return "other", ""


class Run:
    """Everything the report reads, read once."""

    def __init__(self, state: Path) -> None:
        self.state = state
        self.run = read_json(state / "run.json") or {}
        self.flow = read_json(state / "flow.json")
        self.plan = read_json(state / "plan.json")
        self.has_journal = (state / "journal.md").is_file()   # absent: what only it records is "not recorded", never 0
        self.journal = journal_lines(state)
        for ln in self.journal:
            ln["kind"], ln["detail"] = classify(ln["text"])
        chains = {}
        for p in sorted((state / "chains").glob("*/state.json")):
            st = read_json(p)
            if st and isinstance(st.get("steps"), list):
                chains[str(st.get("pr") or p.parent.name)] = st
        order = [p.get("id") for p in ((self.flow or self.plan or {}).get("prs") or []) if isinstance(p, dict)]
        self.prs = [pr for pr in order if pr in chains] + [pr for pr in chains if pr not in order]
        self.chains = chains
        self.registry = {}
        for p in sorted((state / "dispatches").glob("*.json")):
            r = read_json(p)
            if r and r.get("dispatch"):
                self.registry[r["dispatch"]] = r
        self.logs = (state / "logs").is_dir()
        self.collected = {}   # dispatch -> its logs directory
        for meta in sorted((state / "logs").glob("*/*/*/meta.json")) if self.logs else []:
            m = read_json(meta)
            if m and m.get("dispatch"):
                self.collected[m["dispatch"]] = (meta.parent, m)
        self.questions_ev = {}   # dispatch -> [(created, message id)] from the event files' names
        for f in sorted((state / "events").glob("*/*-question-*.json")):
            digits, _, mid = f.stem.partition("-question-")
            created = None
            if len(digits) >= 14:
                d = digits[:14]
                created = f"{d[:4]}-{d[4:6]}-{d[6:8]}T{d[8:10]}:{d[10:12]}:{d[12:14]}Z"
            self.questions_ev.setdefault(f.parent.name, []).append((created, mid))
        self.silent = []   # (rang, pr) from the wake files' names
        for f in sorted(list((state / "wake").glob("*.txt")) + list((state / "wake" / "seen").glob("*.txt"))):
            ns, _, rest = f.stem.partition("-")
            pr, _, kind = rest.rpartition("-")
            if kind == "silent" and ns.isdigit():
                self.silent.append((int(ns) / 1e9, pr if pr and pr != "run" else OUTSIDE))
        self.dispatches = self._dispatches()

    def _dispatches(self) -> "list[dict]":
        """One row per dispatch: the chain's attempts first, in step order, then the ad hoc ones."""
        rows, seen = [], set()
        for pr in self.prs:
            st = self.chains[pr]
            for step in st["steps"]:
                for att in step.get("attempts") or []:
                    d = att.get("dispatch")
                    if not d or d in seen:
                        continue
                    seen.add(d)
                    rows.append(self._row(d, pr, step, att))
        for d, reg in self.registry.items():
            if d in seen:
                continue
            seen.add(d)
            rows.append(self._row(d, reg.get("pr") or OUTSIDE, None, None))
        return rows

    def _row(self, d: str, pr: str, step: "dict | None", att: "dict | None") -> dict:
        reg = self.registry.get(d) or {}
        logdir, meta = self.collected.get(d, (None, {}))
        tokens = read_json(logdir / "tokens.json") if logdir else None
        if att is not None:
            agent, model, effective = att.get("agent") or meta.get("agent"), att.get("model") or meta.get("model"), att.get("effective") or meta.get("effective")
            if not agent and step is not None:
                agent = (step.get("def") or {}).get("agent")
            started, ended = att.get("started"), att.get("ended")
        else:
            agent = reg.get("agent") or meta.get("agent") or ((reg.get("who") or "").split() or [None])[0]
            model = reg.get("model") or meta.get("model") or (((reg.get("who") or "").split() + ["", ""])[1] or None)
            effective = meta.get("effective")
            started, ended = reg.get("started"), reg.get("settled") or meta.get("ended")
        eff_model = effective.get("model") if isinstance(effective, dict) else None
        label = model or agent or NR
        if eff_model and eff_model != (model or ""):
            label = f"{label} -> {eff_model}"
        sess = meta.get("session") or {}
        return {"dispatch": d, "pr": pr, "step": step["id"] if step else "_adhoc", "n": att.get("n") if att else None,
                "adhoc": att is None, "agent": agent, "label": label, "started": started, "ended": ended,
                "duration_s": span(started, ended), "collected": logdir is not None,
                "match": sess.get("match") if logdir is not None else "not collected",
                "why": sess.get("why") or "", "tokens": tokens}


# ---------------------------------------------------------------- the numbers

def add(into: dict, key: str, value: object) -> None:
    """Sum recorded values; a key nobody recorded stays "not recorded"."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        into.setdefault(key, NR)
        return
    into[key] = (0 if into.get(key, NR) == NR else into[key]) + value


def median(xs: "list[int]") -> "int | float":
    m = statistics.median(xs)
    return int(m) if float(m).is_integer() else m


def waits(run: Run, pr: str) -> "list[dict]":
    """The PR's gate waits and pauses, from its journal lines: {kind, step, from, to, s} (to and s None when open)."""
    lines = [ln for ln in run.journal if ln["pr"] == pr]
    out = []
    for i, ln in enumerate(lines):
        if ln["kind"] != "paused":
            continue
        w = {"kind": "gate" if ln["detail"] == "blocked" else "paused", "step": ln["step"], "from": ln["t"], "to": None, "s": None}
        for nxt in lines[i + 1:]:
            if nxt["kind"] == "coordinator":
                w.update(to=nxt["t"], s=int(nxt["e"] - ln["e"]))
                break
            if nxt["kind"] in ("paused", "complete"):
                break
        out.append(w)
    return out


Spans = "list[tuple[float, float]]"


def union(spans: Spans) -> Spans:
    """The time at least one of the spans covers, as sorted spans that do not overlap."""
    out: Spans = []
    for a, b in sorted(x for x in spans if x[1] > x[0]):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def minus(spans: Spans, cut: Spans) -> Spans:
    """The parts of spans (a union) that cut (a union) does not cover."""
    out: Spans = []
    for a, b in spans:
        for c, d in cut:
            if d <= a:
                continue
            if c >= b:
                break
            if c > a:
                out.append((a, c))
            a = d
            if a >= b:
                break
        if a < b:
            out.append((a, b))
    return out


def seconds(spans: Spans) -> int:
    return int(sum(b - a for a, b in spans))


def time_split(run: Run, pr: str) -> dict:
    """A partition of the PR's wall clock: each second goes to one part only, in this order: a worker ran, a gate
    waited, the chain was paused, the rest. So no part is negative and the four add up to the wall clock."""
    st = run.chains[pr]
    done = st.get("ended") if st.get("status") == "done" else None
    wall = span(st.get("created"), done)
    worker, spans = 0, []
    for step in st["steps"]:
        for att in step.get("attempts") or []:
            s = span(att.get("started"), att.get("ended"))
            if att.get("dispatch") and s is not None:
                worker += s
                spans.append((ts_epoch(att["started"]), ts_epoch(att["ended"])))
    ws = waits(run, pr)
    lo, hi = ts_epoch(st.get("created")), ts_epoch(done)

    def within(kind: str) -> Spans:
        sp = [(ts_epoch(w["from"]), ts_epoch(w["to"])) for w in ws if w["kind"] == kind and w["s"] is not None]
        return union([(max(a, lo), min(b, hi)) for a, b in sp] if wall is not None else sp)

    covered = union([(max(a, lo), min(b, hi)) for a, b in spans] if wall is not None else spans)
    gates = minus(within("gate"), covered)
    pauses = minus(minus(within("paused"), covered), gates)
    split = {"worker_covered_s": seconds(covered), "gate_s": seconds(gates), "paused_s": seconds(pauses)}
    split["rest_s"] = NR if wall is None else wall - sum(split.values())
    if not run.has_journal:
        split.update(gate_s=NR, paused_s=NR, rest_s=NR)
    adhoc = [r["duration_s"] for r in run.dispatches if r["adhoc"] and r["pr"] == pr and r["duration_s"] is not None]
    return {"created": st.get("created") or NR, "done": done or NR, "wall_s": NR if wall is None else wall,
            "worker_s": worker, **split, "open_waits": sum(1 for w in ws if w["s"] is None) if run.has_journal else NR,
            "adhoc_s": sum(adhoc)}


def step_instances(run: Run, prs: "list[str]") -> "list[dict]":
    out = []
    for pr in prs:
        for step in run.chains[pr]["steps"]:
            atts = step.get("attempts") or []
            if not atts:
                continue
            spans = [span(a.get("started"), a.get("ended")) for a in atts]
            typ = (step.get("def") or {}).get("type", "worker")
            out.append({"pr": pr, "step": step["id"], "type": typ, "attempts": atts,
                        "duration_s": sum(spans) if all(s is not None for s in spans) else None,
                        "dispatches": [a["dispatch"] for a in atts if a.get("dispatch")],
                        "spec": (step.get("def") or {}).get("spec")})
    return out


def cause_of(att: dict) -> str:
    return att.get("cause") or ("first" if att.get("n") == 1 else NR)


def not_ok_checks(run: Run, prs: "list[str]") -> "dict[tuple[str, str], int]":
    out: "dict[tuple[str, str], int]" = {}
    for ln in run.journal:
        if ln["kind"] == "check" and ln["detail"] == "NOT OK" and ln["pr"] in prs:
            out[(ln["pr"], ln["step"])] = out.get((ln["pr"], ln["step"]), 0) + 1
    return out


def steps_metrics(run: Run) -> dict:
    inst = step_instances(run, run.prs)
    bad = not_ok_checks(run, run.prs)
    out: dict = {}
    for i in inst:
        out.setdefault(i["step"], {"type": i["type"], "items": []})["items"].append(i)
    res = {}
    for sid, g in out.items():
        durs = [(i["duration_s"], i["pr"]) for i in g["items"] if i["duration_s"] is not None]
        causes: dict = {}
        for i in g["items"]:
            for a in i["attempts"]:
                causes[cause_of(a)] = causes.get(cause_of(a), 0) + 1
        mx = max(durs, key=lambda x: x[0]) if durs else None   # the first PR wins a tie
        res[sid] = {"type": g["type"], "instances": len(g["items"]),
                    "median_s": median([d for d, _ in durs]) if durs else NR, "max_s": mx[0] if mx else NR,
                    "max_pr": mx[1] if mx else NR, "attempts": sum(len(i["attempts"]) for i in g["items"]),
                    "by_cause": dict(sorted(causes.items())),
                    "check_not_ok": sum(n for (p, s), n in bad.items() if s == sid) if run.has_journal else NR,
                    "check_not_ok_prs": sorted({p for (p, s) in bad if s == sid}) if run.has_journal else NR}
    return res


def role_of(run: Run, pr: str, step: str, dispatch: str) -> str:
    if (run.registry.get(dispatch) or {}).get("adhoc"):
        return "ad hoc"
    st = run.chains.get(pr)
    for s in (st or {}).get("steps") or []:
        if s["id"] == step:
            return (s.get("def") or {}).get("spec") or step
    return step or NR


def questions(run: Run) -> "list[dict]":
    replies = {ln["detail"]: ln for ln in run.journal if ln["kind"] == "replied"}
    seen: "dict[str, int]" = {}
    out = []
    for ln in run.journal:
        if ln["kind"] != "question":
            continue
        k = seen.get(ln["dispatch"], 0)
        seen[ln["dispatch"]] = k + 1
        evs = run.questions_ev.get(safe(ln["dispatch"])) or []
        created, mid = evs[k] if k < len(evs) else (None, None)
        asked = created or ln["t"]
        rep = replies.get(mid) if mid else None
        out.append({"pr": ln["pr"], "step": ln["step"], "dispatch": ln["dispatch"],
                    "role": role_of(run, ln["pr"], ln["step"], ln["dispatch"]), "message": mid or NR, "asked": asked,
                    "answered": rep["t"] if rep else NR, "answer_s": span(asked, rep["t"]) if rep else NR})
    return out


def rings(run: Run) -> "dict[str, dict[str, int]]":
    """{kind: {pr: n}} from the journal, plus the silent rings from the wake files' names. Without a journal, the kinds
    it records are "not recorded"; the silent rings still come from the wake files."""
    out: "dict[str, dict[str, int] | str]" = {k: {} if run.has_journal or k == "silent" else NR for k in RING_KINDS}

    def count(kind: str, pr: str) -> None:
        out.setdefault(kind, {})
        out[kind][pr] = out[kind].get(pr, 0) + 1

    for ln in run.journal:
        if ln["kind"] == "paused":
            count(PAUSE_KIND.get(ln["detail"], "runner"), ln["pr"])
        elif ln["kind"] == "question":
            count("question", ln["pr"])
        elif ln["kind"] == "message":
            count(ln["detail"], ln["pr"])
        elif ln["kind"] in ("collector", "locked"):
            count(ln["kind"], ln["pr"])
    for _t, pr in run.silent:
        count("silent", pr)
    return out


def tokens_metrics(run: Run, prices: "dict | None") -> dict:
    by_label: dict = {}
    by_model: dict = {}
    provider: dict = {}
    per: list = []
    for r in run.dispatches:
        t = r["tokens"]
        lab = by_label.setdefault(r["label"], {"dispatches": 0, "tokens_files": 0})
        lab["dispatches"] += 1
        row = {"dispatch": r["dispatch"], "pr": r["pr"], "step": r["step"], "label": r["label"], "match": r["match"]}
        if t is None:
            row.update({k: NR for k in TOKEN_FIELDS})
            for k in TOKEN_FIELDS:
                lab.setdefault(k, NR)
            lab.setdefault("subagents", NR)
            per.append(row)
            continue
        lab["tokens_files"] += 1
        for k in TOKEN_FIELDS:
            row[k] = t.get(k, NR) if isinstance(t.get(k), (int, float)) else NR
            add(lab, k, t.get(k))
        sub = t.get("subagents")
        if isinstance(sub, dict):
            if not isinstance(lab.get("subagents"), dict):
                lab["subagents"] = {}
            for k in SUB_FIELDS:
                add(lab["subagents"], k, sub.get(k))
        else:
            lab.setdefault("subagents", NR)
        for model, u in (t.get("by_model") or {}).items():
            m = by_model.setdefault(model, {})
            provider.setdefault(model, t.get("provider"))
            for k in TOKEN_FIELDS:
                add(m, k, (u or {}).get(k))
        per.append(row)
    cost: "dict | str" = NP
    if prices is not None:
        cost = {}
        for model, u in by_model.items():
            p = prices.get(model)
            if not isinstance(p, dict):
                cost[model] = NP
                continue
            c: dict = {}
            for k in PRICE_FIELDS:
                n = u.get(k)
                if k == "input" and provider.get(model) == "codex" and isinstance(n, (int, float)):
                    cr = u.get("cache_read")
                    n = n - cr if isinstance(cr, (int, float)) else NR   # Codex counts cached input inside input
                if not isinstance(n, (int, float)):
                    c[k] = NR
                elif not isinstance(p.get(k), (int, float)):
                    c[k] = NP
                else:
                    c[k] = round(n * p[k] / 1e6, 6)
            nums = [v for v in c.values() if isinstance(v, (int, float))]
            c["total"] = round(sum(nums), 6) if nums else NP
            c["unpriced"] = [k for k in PRICE_FIELDS if not isinstance(c[k], (int, float))]
            cost[model] = c
    return {"by_label": by_label, "by_model": by_model, "dispatches": per, "cost": cost,
            "no_session": [{"dispatch": r["dispatch"], "pr": r["pr"], "step": r["step"], "match": r["match"]}
                           for r in run.dispatches if r["match"] != "unique"]}


FINDINGS_RE = re.compile(r"\bFINDINGS:\s*(\d+)")
VERDICT_RE = re.compile(r"\bVERDICT:\s*([A-Z][A-Z_-]*)")


def review_metrics(run: Run) -> dict:
    rows = []
    for pr in run.prs:
        for step in run.chains[pr]["steps"]:
            for att in step.get("attempts") or []:
                summary = str(att.get("summary") or "")
                f, v = FINDINGS_RE.search(summary), VERDICT_RE.search(summary)
                if not (re.search(r"review|triage", step["id"]) or f):
                    continue
                if not att.get("dispatch"):
                    continue
                rows.append({"pr": pr, "step": step["id"], "n": att.get("n"), "dispatch": att["dispatch"],
                             "findings": int(f.group(1)) if f else NR, "verdict": v.group(1) if v else NR})
    by_step: dict = {}
    for r in rows:
        s = by_step.setdefault(r["step"], {"findings": NR, "recorded": 0, "not_recorded": 0})
        if r["findings"] == NR:
            s["not_recorded"] += 1
        else:
            s["recorded"] += 1
            add(s, "findings", r["findings"])
    return {"attempts": rows, "by_step": by_step}


def flow_metrics(run: Run) -> dict:
    if not run.flow:
        return {"version": NR, "history": [], "started_under": {}}
    hist = [{"v": h.get("v"), "at": h.get("at") or NR, "by": h.get("by") or NR, "note": h.get("note") or "",
             "changes": len(h.get("changes") or [])} for h in run.flow.get("history") or [] if isinstance(h, dict)]
    under: dict = {}
    for pr in run.prs:
        v = (run.chains[pr].get("flow") or {}).get("started_at")
        under.setdefault(str(v) if v is not None else NR, []).append(pr)
    return {"version": run.flow.get("version", NR), "history": hist, "started_under": under}


def lessons(run: Run, steps: dict, qs: "list[dict]", labels: "dict[str, str]") -> "list[dict]":
    inst = [i for i in step_instances(run, run.prs) if i["duration_s"] is not None]
    out = []

    def model(ds: "list[str]") -> str:
        return ", ".join(dict.fromkeys(labels[d] for d in ds if d in labels)) or "-"

    if inst:
        s = max(inst, key=lambda i: i["duration_s"])
        out.append({"lesson": "slowest step", "value": s["duration_s"], "unit": "s", "pr": s["pr"], "step": s["step"],
                    "dispatch": ", ".join(s["dispatches"]) or "-", "model": model(s["dispatches"]),
                    "record": f"chains/{s['pr']}/state.json steps[{s['step']}].attempts"})
    all_inst = step_instances(run, run.prs)
    if all_inst and max(len(i["attempts"]) for i in all_inst) > 1:
        s = max(all_inst, key=lambda i: len(i["attempts"]))
        out.append({"lesson": "most-retried step", "value": len(s["attempts"]), "unit": "attempts", "pr": s["pr"],
                    "step": s["step"], "dispatch": ", ".join(s["dispatches"]) or "-", "model": model(s["dispatches"]),
                    "record": f"chains/{s['pr']}/state.json steps[{s['step']}].attempts"})
    else:
        out.append({"lesson": "most-retried step", "value": NR, "unit": "", "pr": "-", "step": "-", "dispatch": "-",
                    "model": "-", "record": "no step ran twice"})
    roles: "dict[str, list[dict]]" = {}
    for q in qs:
        roles.setdefault(q["role"], []).append(q)
    if roles:
        role, rq = max(roles.items(), key=lambda kv: len(kv[1]))
        out.append({"lesson": "role that asked the most questions", "value": len(rq), "unit": "questions",
                    "role": role, "pr": rq[0]["pr"], "step": rq[0]["step"], "dispatch": rq[0]["dispatch"],
                    "model": model([rq[0]["dispatch"]]), "record": "journal.md question lines"})
    else:
        out.append({"lesson": "role that asked the most questions", "value": NR, "unit": "", "role": "-", "pr": "-",
                    "step": "-", "dispatch": "-", "model": "-",
                    "record": "no question in journal.md" if run.has_journal else "no journal.md"})
    gates = [(w, pr) for pr in run.prs for w in waits(run, pr) if w["kind"] == "gate" and w["s"] is not None]
    if gates:
        w, pr = max(gates, key=lambda x: x[0]["s"])
        out.append({"lesson": "longest wait at a gate", "value": w["s"], "unit": "s", "pr": pr, "step": w["step"],
                    "dispatch": "-", "model": "-", "record": f"journal.md paused: blocked at {w['from']} .. coordinator at {w['to']}"})
    else:
        out.append({"lesson": "longest wait at a gate", "value": NR, "unit": "", "pr": "-", "step": "-", "dispatch": "-",
                    "model": "-", "record": "no gate wait with an end in journal.md" if run.has_journal else "no journal.md"})
    return out


def per_pr(run: Run, pr: str, ring_table: dict, qs: "list[dict]") -> dict:
    bad = not_ok_checks(run, [pr])
    steps = {i["step"]: {"duration_s": NR if i["duration_s"] is None else i["duration_s"], "attempts": len(i["attempts"]),
                         "check_not_ok": bad.get((pr, i["step"]), 0) if run.has_journal else NR}
             for i in step_instances(run, [pr])}
    return {"steps": steps, "rings": {k: v.get(pr, 0) if isinstance(v, dict) else v for k, v in ring_table.items()},
            "questions": sum(1 for q in qs if q["pr"] == pr) if run.has_journal else NR}


def metrics(run: Run, prices: "dict | None" = None) -> dict:
    qs = questions(run)
    ring_table = rings(run)
    steps = steps_metrics(run)
    times = {pr: time_split(run, pr) for pr in run.prs}
    total: dict = {}
    for k in ("wall_s", "worker_s", "worker_covered_s", "gate_s", "paused_s", "rest_s", "adhoc_s", "open_waits"):
        vals = [t[k] for t in times.values()]
        total[k] = sum(vals) if vals and all(isinstance(v, int) for v in vals) else NR
    times["total"] = total
    outside = [r for r in run.dispatches if r["pr"] not in run.prs]
    answered = [q["answer_s"] for q in qs if isinstance(q["answer_s"], int)]
    by_role: dict = {}
    for q in qs:
        by_role[q["role"]] = by_role.get(q["role"], 0) + 1
    labels = {r["dispatch"]: r["label"] for r in run.dispatches}
    stamps = [ln["e"] for ln in run.journal]
    for st in run.chains.values():
        stamps += [e for e in (ts_epoch(st.get("created")), ts_epoch(st.get("ended"))) if e is not None]
    return {
        "report": 1,
        "run": {"run": run.run.get("run") or NR, "title": (run.flow or run.plan or {}).get("title") or NR,
                "prs": len(run.prs), "dispatches": len(run.dispatches),
                "chain_dispatches": sum(1 for r in run.dispatches if not r["adhoc"]),
                "adhoc_dispatches": sum(1 for r in run.dispatches if r["adhoc"]),
                "collected_sessions": sum(1 for r in run.dispatches if r["match"] == "unique"),
                "logs": run.logs, "from": iso(min(stamps)) if stamps else NR, "to": iso(max(stamps)) if stamps else NR},
        "time": times,
        "outside": {"adhoc_s": sum(r["duration_s"] for r in outside if r["duration_s"] is not None),
                    "dispatches": [r["dispatch"] for r in outside]},
        "steps": steps,
        "interruptions": {"rings": {k: dict(v, total=sum(v.values())) if isinstance(v, dict) else v
                                    for k, v in ring_table.items()},
                          "questions": qs if run.has_journal else NR,
                          "questions_by_role": by_role if run.has_journal else NR,
                          "answer_median_s": median(answered) if answered else NR,
                          "answer_max_s": max(answered) if answered else NR,
                          "unanswered": sum(1 for q in qs if q["answer_s"] == NR) if run.has_journal else NR},
        "tokens": tokens_metrics(run, prices) if run.logs else {"by_label": NR, "by_model": NR, "dispatches": NR,
                                                               "cost": NR, "no_session": NR},
        "review": review_metrics(run),
        "flow": flow_metrics(run),
        "lessons": lessons(run, steps, qs, labels),
        "per_pr": {pr: per_pr(run, pr, ring_table, qs) for pr in run.prs},
    }


# ---------------------------------------------------------------- the page

def esc(x: object) -> str:
    return html.escape(str(x), quote=True)


def num(x: object) -> str:
    return NR if x is None else str(x)


def dur(x: object) -> str:
    """Seconds as the record has them, then the same in hours and minutes."""
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return esc(x)
    s = int(abs(x))
    human = f"{s // 3600}h {s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m {s % 60:02d}s"
    return f'{x} s <span class="mute">({"-" if x < 0 else ""}{human})</span>'


def cell(x: object) -> str:
    cls = ' class="nr"' if x in (NR, NP) else ""
    return f"<td{cls}>{esc(x)}</td>"


def table(head: "list[str]", rows: "list[list[str]]", source: str) -> str:
    th = "".join(f"<th>{esc(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(c if c.startswith("<td") else f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    if not rows:
        body = f'<tr><td colspan="{len(head)}" class="nr">none recorded</td></tr>'
    return f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div><p class="src">from {esc(source)}</p>'


PALETTE = ("var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)")


def hbars(rows: "list[tuple[str, list[float]]]", legend: "list[str]", unit: str = "") -> str:
    """Horizontal stacked bars, one per row, scaled to the largest total. Inline SVG."""
    if not rows:
        return ""
    biggest = max((sum(max(0.0, v) for v in vals) for _, vals in rows), default=0) or 1
    w, lab, h, gap = 640, 150, 18, 8
    height = len(rows) * (h + gap) + 4
    parts = [f'<svg class="chart" viewBox="0 0 {w + lab + 90} {height}" role="img" xmlns="http://www.w3.org/2000/svg">']
    for i, (name, vals) in enumerate(rows):
        y = i * (h + gap) + 2
        parts.append(f'<text x="{lab - 8}" y="{y + 13}" text-anchor="end" class="lab">{esc(name)}</text>')
        x = float(lab)
        for j, v in enumerate(vals):
            if v <= 0:
                continue
            bw = v / biggest * w
            parts.append(f'<rect x="{x:.1f}" y="{y}" width="{bw:.1f}" height="{h}" rx="2" fill="{PALETTE[j % 5]}">'
                         f'<title>{esc(legend[j] if j < len(legend) else "")}: {esc(v)}{esc(unit)}</title></rect>')
            x += bw
        parts.append(f'<text x="{x + 6:.1f}" y="{y + 13}" class="lab mute">{esc(round(sum(vals), 2))}{esc(unit)}</text>')
    parts.append("</svg>")
    key = "".join(f'<span class="key"><i style="background:{PALETTE[j % 5]}"></i>{esc(l)}</span>' for j, l in enumerate(legend))
    return f'<figure>{"".join(parts)}<figcaption>{key}</figcaption></figure>'


def timeline(run: Run, pr: str) -> str:
    """One horizontal bar per attempt, gate waits and pauses as their own bars; gaps stay empty."""
    st = run.chains[pr]
    items = []
    for step in st["steps"]:
        for att in step.get("attempts") or []:
            s, e = ts_epoch(att.get("started")), ts_epoch(att.get("ended"))
            if s is None:
                continue
            kind = "script" if (step.get("def") or {}).get("type") == "script" else (
                "worker" if att.get("outcome") in (None, "succeeded") else "failed")
            items.append((f"{step['id']} #{att.get('n')}", s, e, kind))
    for w in waits(run, pr):
        s, e = ts_epoch(w["from"]), ts_epoch(w["to"])
        items.append((f"{w['step'] or '-'} {'gate' if w['kind'] == 'gate' else 'paused'}", s, e, w["kind"]))
    for r in run.dispatches:
        if r["adhoc"] and r["pr"] == pr and ts_epoch(r["started"]) is not None:
            items.append(("ad hoc", ts_epoch(r["started"]), ts_epoch(r["ended"]), "adhoc"))
    if not items:
        return ""
    items.sort(key=lambda x: x[1])
    t0 = min([ts_epoch(st.get("created")) or items[0][1]] + [i[1] for i in items])
    t1 = max([ts_epoch(st.get("ended")) or 0] + [i[2] or i[1] for i in items])
    total = max(1.0, t1 - t0)
    w, lab, h, gap = 640, 150, 12, 5
    height = len(items) * (h + gap) + 22
    color = {"worker": "var(--c1)", "script": "var(--c4)", "failed": "var(--bad)", "gate": "var(--c2)",
             "paused": "var(--c3)", "adhoc": "var(--c5)"}
    parts = [f'<svg class="chart" viewBox="0 0 {w + lab + 20} {height}" role="img" xmlns="http://www.w3.org/2000/svg">']
    for k in range(5):
        x = lab + w * k / 4
        parts.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height - 16}" class="grid"/>'
                     f'<text x="{x:.1f}" y="{height - 4}" text-anchor="middle" class="lab mute">{esc(iso(t0 + total * k / 4)[11:16])}</text>')
    for i, (name, s, e, kind) in enumerate(items):
        y = i * (h + gap) + 2
        x = lab + (s - t0) / total * w
        bw = max(2.0, ((e if e is not None else t1) - s) / total * w)
        open_ = "" if e is not None else ' stroke-dasharray="3 2" stroke="var(--ink)"'
        parts.append(f'<text x="{lab - 6}" y="{y + 10}" text-anchor="end" class="lab">{esc(name)}</text>'
                     f'<rect x="{x:.1f}" y="{y}" width="{bw:.1f}" height="{h}" rx="2" fill="{color[kind]}"{open_}>'
                     f'<title>{esc(name)}: {esc(iso(s))} .. {esc(iso(e) if e is not None else "no end recorded")}</title></rect>')
    parts.append("</svg>")
    key = "".join(f'<span class="key"><i style="background:{color[k]}"></i>{k}</span>' for k in color)
    return f'<figure>{"".join(parts)}<figcaption>{key} · a dashed bar has no recorded end</figcaption></figure>'


def time_rows(times: dict, prs: "list[str]") -> "list[list[str]]":
    return [[esc(pr), esc(t.get("created", "")), esc(t.get("done", "")), dur(t["wall_s"]), dur(t["worker_s"]), dur(t["worker_covered_s"]), dur(t["gate_s"]),
             dur(t["paused_s"]), dur(t["rest_s"]), num(t["open_waits"]), dur(t["adhoc_s"])]
            for pr, t in ((p, times[p]) for p in prs)]


TIME_HEAD = ["PR", "chain created", "done", "wall clock", "workers (sum)", "workers (covered)", "at gates", "paused", "the rest", "open waits", "ad hoc workers"]
TIME_SRC = "chains/<pr>/state.json (created, ended, attempts started..ended), journal.md (paused / coordinator lines), dispatches/*.json (ad hoc)"


def bars_of_time(times: dict, prs: "list[str]") -> str:
    rows = []
    for pr in prs:
        t = times[pr]
        rows.append((pr, [t[k] if isinstance(t[k], int) else 0 for k in ("worker_covered_s", "gate_s", "paused_s", "rest_s")]))
    return hbars(rows, ["workers (covered)", "at gates", "paused", "the rest"], " s")


def rings_table(ring_table: dict, cols: "list[str]") -> str:
    rows = [[esc(k)] + ([num(v.get(c, 0)) for c in cols] + [num(v.get("total", sum(v.values())))] if isinstance(v, dict)
                        else [cell(v)] * (len(cols) + 1)) for k, v in ring_table.items()]
    return table(["ring"] + cols + ["total"], rows, "journal.md (paused, question, message and collector lines), wake/ file names (silent)")


def page(m: dict, run: Run, state_label: str, only_pr: "str | None" = None) -> str:
    r = m["run"]
    out = []
    out.append(f"<header><h1>Run report · {esc(r['title'])}</h1><p class=\"sub\">run <code>{esc(r['run'])}</code> · state "
               f"<code>{esc(state_label)}</code> · {esc(r['from'])} → {esc(r['to'])}</p>"
               f'<div class="kpis">'
               + "".join(f'<div class="kpi"><div class="lab">{esc(k)}</div><div class="big">{esc(v)}</div></div>' for k, v in (
                   ("PRs", r["prs"]), ("dispatches", r["dispatches"]), ("of them in chains", r["chain_dispatches"]),
                   ("ad hoc", r["adhoc_dispatches"]), ("collected sessions", r["collected_sessions"]),
                   ("logs/", "collected" if r["logs"] else "absent: the collector never ran")))
               + "</div></header>")
    sections = [] if only_pr else ["a", "b", "c", "d", "e", "f", "g"]
    if "a" in sections:
        t = m["time"]
        out.append("<section><h2>a · Where the time went, per PR</h2>" + bars_of_time(t, run.prs)
                   + table(TIME_HEAD, time_rows(t, run.prs + ["total"]), TIME_SRC)
                   + f'<p class="note">Ad hoc workers outside any PR: {dur(m["outside"]["adhoc_s"])}. A wait is open when no '
                   "coordinator line ends it before the next pause; open waits are not counted.</p></section>")
    if "b" in sections:
        s = m["steps"]
        chart = hbars([(sid, [v["median_s"] if isinstance(v["median_s"], (int, float)) else 0]) for sid, v in s.items()],
                      ["median duration"], " s")
        rows = [[esc(sid), esc(v["type"]), num(v["instances"]), dur(v["median_s"]), dur(v["max_s"]), esc(v["max_pr"]),
                 num(v["attempts"]), esc(", ".join(f"{k} {n}" for k, n in v["by_cause"].items())), num(v["check_not_ok"]),
                 esc(", ".join(v["check_not_ok_prs"]) if isinstance(v["check_not_ok_prs"], list) else v["check_not_ok_prs"])]
                for sid, v in s.items()]
        out.append("<section><h2>b · Steps across PRs</h2>" + chart + table(
            ["step", "type", "PRs", "median", "max", "max in", "attempts", "by cause", "checks NOT OK", "in PRs"], rows,
            "chains/<pr>/state.json attempts (started, ended, cause), journal.md check lines") + "</section>")
    if "c" in sections:
        i = m["interruptions"]
        chart = hbars([(k, [v["total"] if isinstance(v, dict) else 0]) for k, v in i["rings"].items()], ["rings"])
        q_rows = [[esc(q["pr"]), esc(q["step"]), esc(q["role"]), f"<code>{esc(q['dispatch'])}</code>", esc(q["asked"]),
                   esc(q["answered"]), cell(q["answer_s"]) if q["answer_s"] == NR else dur(q["answer_s"])]
                  for q in (i["questions"] if isinstance(i["questions"], list) else [])]
        by_role = i["questions_by_role"] if isinstance(i["questions_by_role"], dict) else {NR: NR}
        out.append("<section><h2>c · Interruptions</h2>" + chart + rings_table(i["rings"], run.prs + [OUTSIDE])
                   + "<h3>Questions per role</h3>" + table(["role (spec)", "questions"],
                                                           [[esc(k), num(v)] for k, v in by_role.items()], "journal.md question lines")
                   + f'<p class="note">Time to the coordinator\'s answer: median {dur(i["answer_median_s"])}, max {dur(i["answer_max_s"])}, '
                   f'unanswered {i["unanswered"]}.</p>'
                   + table(["PR", "step", "role", "dispatch", "asked", "answered", "time to answer"], q_rows,
                           "events/<dispatch>/*-question-<id>.json (names only), journal.md replied lines"))
        out.append("</section>")
    if "d" in sections:
        out.append(tokens_section(m["tokens"]))
    if "e" in sections:
        rv = m["review"]
        rows = [[esc(x["pr"]), esc(x["step"]), num(x["n"]), f"<code>{esc(x['dispatch'])}</code>", cell(x["findings"]),
                 cell(x["verdict"])] for x in rv["attempts"]]
        by = [[esc(k), cell(v["findings"]), num(v["recorded"]), num(v["not_recorded"])] for k, v in rv["by_step"].items()]
        out.append("<section><h2>e · Review yield</h2>"
                   + hbars([(k, [v["findings"] if isinstance(v["findings"], int) else 0]) for k, v in rv["by_step"].items()], ["findings"])
                   + table(["step", "findings", "attempts with a FINDINGS line", "attempts without"], by,
                           "chains/<pr>/state.json attempts[].summary: the FINDINGS number and the VERDICT word only")
                   + table(["PR", "step", "attempt", "dispatch", "findings", "verdict"], rows, "the same") + "</section>")
    if "f" in sections:
        f = m["flow"]
        hist = [[num(h["v"]), esc(h["at"]), esc(h["by"]), num(h["changes"]), esc(h["note"])] for h in f["history"]]
        changes = []
        for h in (run.flow or {}).get("history") or []:
            for c in (h.get("changes") or [])[:12]:
                changes.append([num(h.get("v")), esc(c)])
        under = [[esc(v), esc(", ".join(prs))] for v, prs in f["started_under"].items()]
        out.append(f"<section><h2>f · The flow's history (now v{esc(f['version'])})</h2>"
                   + table(["version", "at", "by", "changes", "note"], hist, "flow.json history")
                   + table(["version", "what changed"], changes, "flow.json history[].changes")
                   + table(["started under version", "PRs"], under, "chains/<pr>/state.json flow.started_at") + "</section>")
    if "g" in sections:
        rows = [[esc(x["lesson"]), cell(x["value"]) if x["value"] == NR else f"{esc(x['value'])} {esc(x['unit'])}",
                 esc(x.get("role", "")), esc(x["pr"]), esc(x["step"]), f"<code>{esc(x['dispatch'])}</code>", esc(x["model"]),
                 esc(x["record"])] for x in m["lessons"]]
        out.append('<section id="lessons"><h2>g · Lessons for the planner</h2>'
                   + table(["lesson", "value", "role", "PR", "step", "dispatch", "model (requested -> effective)", "record"], rows,
                           "sections a to c") + "</section>")
    for pr in run.prs:
        if only_pr and pr != only_pr:
            continue
        out.append(pr_section(m, run, pr))
    outside = [x for x in run.dispatches if x["pr"] not in run.prs]
    if not only_pr or only_pr == OUTSIDE:
        out.append(outside_section(m, outside))
    return PAGE.replace("%TITLE%", esc(f"Run report · {r['title']}")).replace("%BODY%", "\n".join(out))


def tokens_section(t: dict) -> str:
    if t["by_label"] == NR:
        return ('<section><h2>d · Tokens and cost</h2><p class="nr">not recorded: there is no logs/ directory; the collector '
                "never ran for this run (router.py collect --all gathers it).</p></section>")
    lab = t["by_label"]
    chart = hbars([(k, [v.get(f) if isinstance(v.get(f), int) else 0 for f in PRICE_FIELDS]) for k, v in lab.items()],
                  ["input", "output", "cache creation", "cache read"])
    rows = [[esc(k), num(v["dispatches"]), num(v["tokens_files"])] + [cell(v.get(f, NR)) for f in TOKEN_FIELDS]
            for k, v in lab.items()]
    sub = []
    for k, v in lab.items():
        s = v.get("subagents", NR)
        sub.append([esc(k)] + ([cell(s.get(f, NR)) for f in SUB_FIELDS] if isinstance(s, dict) else [cell(NR)] * len(SUB_FIELDS)))
    bym = [[esc(k)] + [cell(v.get(f, NR)) for f in TOKEN_FIELDS] for k, v in t["by_model"].items()]
    per = [[f"<code>{esc(d['dispatch'])}</code>", esc(d["pr"]), esc(d["step"]), esc(d["label"]), cell(d["match"])]
           + [cell(d[f]) for f in TOKEN_FIELDS] for d in t["dispatches"]]
    if t["cost"] == NP:
        cost = f'<p class="nr">{NP}: give --prices &lt;json&gt; with per-model prices per million tokens.</p>'
    else:
        crow = []
        for k, v in t["cost"].items():
            crow.append([esc(k)] + ([cell(NP)] * 6 if v == NP else
                                    [cell(v[f]) for f in PRICE_FIELDS] + [cell(v["total"]), esc(", ".join(v["unpriced"]) or "-")]))
        cost = table(["model", "input", "output", "cache creation", "cache read", "total", "not in the total"], crow,
                     "--prices × the by-model tokens (a Codex input is priced without its cached part)")
    ns = [[f"<code>{esc(d['dispatch'])}</code>", esc(d["pr"]), esc(d["step"]), cell(d["match"])] for d in t["no_session"]]
    return ("<section><h2>d · Tokens and cost</h2>" + chart
            + table(["model asked (-> launched)", "dispatches", "with tokens.json"] + list(TOKEN_FIELDS), rows,
                    "logs/<pr>/<step>/<dispatch>/tokens.json totals (subagents included), labelled from state.json attempts and meta.json")
            + "<h3>The subagents' share of those totals</h3>"
            + table(["model asked (-> launched)"] + list(SUB_FIELDS), sub, "tokens.json subagents")
            + "<h3>By the model the session reported</h3>" + table(["model"] + list(TOKEN_FIELDS), bym, "tokens.json by_model")
            + "<h3>Cost</h3>" + cost
            + "<h3>Dispatches with no session match</h3>" + table(["dispatch", "PR", "step", "match"], ns, "logs/*/*/<dispatch>/meta.json session.match")
            + "<details><summary>Every dispatch</summary>"
            + table(["dispatch", "PR", "step", "model", "session"] + list(TOKEN_FIELDS), per, "tokens.json, meta.json")
            + "</details></section>")


def pr_section(m: dict, run: Run, pr: str) -> str:
    t = {pr: m["time"][pr]}
    p = m["per_pr"][pr]
    steps = [[esc(sid), dur(v["duration_s"]), num(v["attempts"]), num(v["check_not_ok"])] for sid, v in p["steps"].items()]
    adhoc = [x for x in run.dispatches if x["adhoc"] and x["pr"] == pr]
    ad = [[f"<code>{esc(x['dispatch'])}</code>", esc(x["label"]), dur(x["duration_s"]) if x["duration_s"] is not None else cell(NR),
           cell(x["match"])] for x in adhoc]
    rings = {k: {pr: v} if isinstance(v, int) else v for k, v in p["rings"].items()}
    return (f'<section class="pr" id="pr-{esc(pr)}"><h2>PR {esc(pr)}</h2>' + timeline(run, pr)
            + "<h3>a · time</h3>" + bars_of_time(t, [pr]) + table(TIME_HEAD, time_rows(t, [pr]), TIME_SRC)
            + "<h3>b · steps</h3>" + table(["step", "duration (all attempts)", "attempts", "checks NOT OK"], steps,
                                         f"chains/{pr}/state.json, journal.md check lines")
            + "<h3>c · interruptions</h3>" + rings_table(rings, [pr]) + f'<p class="note">questions: {p["questions"]}</p>'
            + ("<h3>ad hoc workers</h3>" + table(["dispatch", "model", "duration", "session"], ad, "dispatches/*.json, logs/<pr>/_adhoc/") if ad else "")
            + "</section>")


def outside_section(m: dict, outside: "list[dict]") -> str:
    if not outside:
        return ""
    rows = [[f"<code>{esc(x['dispatch'])}</code>", esc(x["label"]), dur(x["duration_s"]) if x["duration_s"] is not None else cell(NR),
             cell(x["match"])] for x in outside]
    return ('<section class="pr" id="pr-outside"><h2>Outside any PR</h2>'
            + table(["dispatch", "model", "duration", "session"], rows, "dispatches/*.json, logs/_none/_adhoc/")
            + f'<p class="note">ad hoc worker time: {dur(m["outside"]["adhoc_s"])}</p></section>')


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%TITLE%</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2433;--mute:#667085;--line:#e3e7ee;--bad:#c43d32;
--c1:#3b6fd8;--c2:#d99a1e;--c3:#c4574c;--c4:#3f9b6a;--c5:#8a63c9}
@media (prefers-color-scheme:dark){:root{--bg:#11141a;--card:#191e27;--ink:#e6e9ef;--mute:#98a2b3;--line:#2a3140;
--bad:#ff8f85;--c1:#82b1ff;--c2:#f2b95f;--c3:#ff8f85;--c4:#63d394;--c5:#c3a3ff}}
*{box-sizing:border-box}
body{margin:0;padding:20px 16px 48px;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto}
h1{font-size:21px;margin:0 0 4px}h2{font-size:16px;margin:30px 0 10px}h3{font-size:13px;color:var(--mute);margin:18px 0 6px;text-transform:uppercase;letter-spacing:.05em}
.sub,.mute,.src,.note{color:var(--mute)}.src{font-size:11.5px;margin:2px 0 12px}.note{font-size:12.5px}
code{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:14px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.kpi .lab{font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--mute)}.kpi .big{font-size:18px;font-weight:650}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:6px 16px 10px;margin-top:16px}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top;white-space:nowrap}
th{color:var(--mute);font-weight:600}td.nr,.nr{color:var(--mute);font-style:italic}
figure{margin:8px 0 6px}svg.chart{width:100%;height:auto;max-width:900px}
svg .lab{font-size:11px;fill:var(--ink)}svg .lab.mute{fill:var(--mute)}svg .grid{stroke:var(--line)}
figcaption{font-size:11.5px;color:var(--mute)}.key{margin-right:12px;white-space:nowrap}
.key i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
details summary{cursor:pointer;color:var(--mute);margin:8px 0}
</style></head><body><main>
%BODY%
<p class="src">Every number comes from the record named under its table; "not recorded" means that record is missing. No transcript text is shown.</p>
</main></body></html>
"""


# ---------------------------------------------------------------- command line

def main(argv: "list[str] | None" = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0 if argv else 2
    ap = argparse.ArgumentParser(prog="report.py", add_help=False)
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--metrics")
    ap.add_argument("--prices")
    ap.add_argument("--pr")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    state = Path(a.state)
    if not state.is_dir():
        print(f"report: no state directory: {state}", file=sys.stderr)
        return 1
    prices = None
    if a.prices:
        prices = read_json(Path(a.prices))
        if prices is None:
            print(f"report: the prices file is not a JSON object: {a.prices}", file=sys.stderr)
            return 1
    run = Run(state)
    if a.pr and a.pr not in run.prs and a.pr != OUTSIDE:
        print(f"report: no chain {a.pr}; PRs: {', '.join(run.prs) or 'none'}", file=sys.stderr)
        return 1
    m = metrics(run, prices)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page(m, run, a.state, a.pr))
    if a.metrics:
        Path(a.metrics).parent.mkdir(parents=True, exist_ok=True)
        Path(a.metrics).write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
    r = m["run"]
    print(f"OK report {out} · {r['prs']} PRs · {r['dispatches']} dispatches ({r['adhoc_dispatches']} ad hoc) · "
          f"{r['collected_sessions']} collected sessions" + ("" if r["logs"] else " · no logs/: tokens not recorded"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
