#!/usr/bin/env python3
"""The owner's view of a router run: a screen of text, and one HTML page that reloads itself.

Rendering only. router.py gathers the data (progress_data) and calls text() and html_page(). Nothing here reads
the state directory or calls Orca, and router.py catches whatever this raises: a mistake in the view cannot stop
a daemon.

The data. Every time is UTC, "%Y-%m-%dT%H:%M:%SZ", or "" when there is none:
  written, run, objective, title, state_dir, page, refresh_s, has_plan
  flow       {version, slots, start} when a flow is loaded, else None; its PRs are then the plan
  mailbox    {alive, last}                 the daemon's pid or 0, and its last delivery
  rows       one per inner PR, the plan's (or the flow's) first, then chains it does not name:
             {id, title, part, state, created, ended, done, total, at, why, since, picked_up, url, steps, waits}
             state: not started | running | at a gate | paused | stopped | runner gone | done
             waits: what a flow PR that is not started waits for ("after A1", "waiting for: WT", "ready"), else ""
             steps: [{id, type, status, group, n, started, ended, who, note}]
  workers    what runs now: {kind (worker | script | ad hoc), pr, step, title, who, n, started, dispatch,
             heartbeat, phase}
  rings      rings the coordinator has not picked up: {rang, pr, kind, line}
  questions  not answered: {id, pr, step, asked, text}
  events     the journal's last lines, oldest first: {t, pr, step, text}
"""
import datetime
import html
import time

STEP_GLYPH = {"done": "✓", "skipped": "–", "running": "▶", "starting": "▶", "script": "▶", "recheck": "▶",
              "blocked": "◆", "pending": "·"}                      # any other status is a failure: ✗
STEP_CLASS = {"done": "ok", "skipped": "skip", "running": "run", "starting": "run", "script": "run", "recheck": "run",
              "blocked": "wait", "pending": "idle"}
ROW_GLYPH = {"done": "✓", "running": "▶", "at a gate": "◆", "paused": "✗", "stopped": "■", "runner gone": "✗",
             "not started": "·"}
ROW_CLASS = {"done": "ok", "running": "run", "at a gate": "wait", "paused": "bad", "stopped": "wait",
             "runner gone": "bad", "not started": "idle"}
WAITING = ("at a gate", "paused", "stopped", "runner gone")
HEARTBEAT_LATE_S = 600    # a heartbeat is due every 5 min; the router rings at ROUTER_SILENT_MIN
RELOAD_S = 15


def epoch(ts: str) -> float:
    try:
        return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0.0


def dur(seconds: float) -> str:
    m = int(max(0.0, seconds) // 60)
    if m < 1:
        return "<1 min"
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60} min"


def ago(ts: str, now: float) -> str:
    if not epoch(ts):
        return "never"
    s = now - epoch(ts)
    return "just now" if s < 60 else dur(s) + " ago"


def took(a: str, b: str) -> str:
    return dur(epoch(b) - epoch(a)) if epoch(a) and epoch(b) else ""


def clock(ts: str) -> str:
    """Local wall-clock time of a UTC stamp, for the event list."""
    e = epoch(ts)
    return datetime.datetime.fromtimestamp(e).strftime("%d %b %H:%M") if e else ""


def counts(rows: "list[dict]") -> str:
    order = ("done", "running", "at a gate", "paused", "stopped", "runner gone", "not started")
    n = {k: sum(1 for r in rows if r["state"] == k) for k in order}
    return " · ".join(f"{n[k]} {k}" for k in order if n[k])


def at_text(r: dict) -> str:
    return " ∥ ".join(r["at"]) or "-"


def owner_action(r: dict) -> str:
    """What a row that is not moving waits for."""
    if r["state"] == "runner gone":
        return f"its runner is gone: router.py resume {r['id']}"
    if r["state"] == "stopped":
        return f"stopped with router.py stop: router.py resume {r['id']}"
    return "the coordinator decides" + ("" if r.get("picked_up", True) else "; it has NOT picked the ring up yet")


# ---------------------------------------------------------------- text

def text(d: dict) -> str:
    now = time.time()
    rows = d["rows"]
    out = [f"{d.get('title') or d.get('objective') or 'router run'} · {d.get('run') or 'no run'}"
           + (f" · flow v{d['flow']['version']}" if d.get("flow") else ""),
           f"{sum(1 for r in rows if r['state'] == 'done')} of {len(rows)} inner PRs done"
           + (f" ({counts(rows)})" if rows else "")
           + ("" if d.get("has_plan") else " · no plan recorded, so only started PRs are listed: router.py plan <file>"),
           f"mailbox daemon {'alive' if d['mailbox']['alive'] else 'NOT RUNNING: router.py init'}"
           f" · last delivery {ago(d['mailbox']['last'], now)}"]
    part = None
    width = max([len(r["id"]) for r in rows] + [4])
    for r in rows:
        if r["part"] != part:
            part = r["part"]
            out += ["", part or "inner PRs"]
        line = f"  {ROW_GLYPH.get(r['state'], '?')} {r['id']:<{width}}  {r['state']}"
        if r["state"] == "done":
            line += f" · took {took(r['created'], r['ended']) or '?'}"
        elif r["state"] != "not started":
            line += f" · step {min(r['done'] + 1, r['total'])} of {r['total']}: {at_text(r)} · {dur(now - epoch(r['created']))} so far"
        elif r.get("waits"):
            line += f" · {r['waits']}"
        out.append(line + (f" · {r['title']}" if r["title"] else ""))
        if r["state"] in ("done", "not started"):
            continue
        out.append("      " + "  ".join(f"{STEP_GLYPH.get(s['status'], '✗')} {s['id']}" for s in r["steps"]))
        if r["state"] in WAITING:
            out.append(f"      waiting{' ' + dur(now - epoch(r['since'])) if epoch(r.get('since', '')) else ''}: {owner_action(r)}"
                       + (f" · {r['why']}" if r["why"] else ""))
        for w in d["workers"]:
            if w["pr"] == r["id"] and w["kind"] != "ad hoc":
                out.append("      " + worker_text(w, now))
    adhoc = [w for w in d["workers"] if w["kind"] == "ad hoc"]
    if adhoc:
        out += ["", "workers outside a chain"] + ["  " + worker_text(w, now) for w in adhoc]
    if d["questions"]:
        out += ["", "questions not answered"]
        out += [f"  {q['pr'] or '-'} {q['step'] or '-'} · asked {ago(q['asked'], now)} · {q['text']}" for q in d["questions"]]
    if d["rings"]:
        out += ["", "rings the coordinator has not picked up (is its router.py wait running?)"]
        out += [f"  rang {ago(g['rang'], now)} · {g['line']}" for g in d["rings"]]
    if d["events"]:
        out += ["", "last events"]
        out += [f"  {clock(e['t'])}  {e['pr']:<{width}}  {e['step']:<14}  {e['text']}" for e in d["events"][-8:]]
    out += ["", f"page: {d['page']}   (open it once: it reloads itself every {RELOAD_S} s)"]
    return "\n".join(out)


def worker_text(w: dict, now: float) -> str:
    if w["kind"] == "script":
        return f"script  {w['step']} · no worker: the router runs it · started {ago(w['started'], now)}"
    line = f"worker  {w['title']} · {w['who'] or 'agent not recorded'}"
    if w["n"] > 1:
        line += f" · attempt {w['n']}"
    if not w["dispatch"]:
        return line + f" · starting since {ago(w['started'], now)}"
    return (line + f" · started {ago(w['started'], now)} · heartbeat {ago(w['heartbeat'], now) if w['heartbeat'] else 'none yet'}"
            + (f" ({w['phase']})" if w["phase"] else "") + f" · {w['dispatch']}")


# ---------------------------------------------------------------- HTML

CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#1b2230;--mute:#667085;--line:#e2e5eb;--ok:#17794a;--okbg:#e2f4e9;--run:#1b5bcc;
--runbg:#e3ecfc;--wait:#8f5400;--waitbg:#fdefd2;--bad:#b3261e;--badbg:#fbe3e1;--idle:#8a94a6;--idlebg:#eceef2}
@media (prefers-color-scheme:dark){:root{--bg:#11141a;--card:#191e27;--ink:#e6e9ef;--mute:#98a2b3;--line:#2a3140;
--ok:#63d394;--okbg:#163423;--run:#82b1ff;--runbg:#16284a;--wait:#f2b95f;--waitbg:#3a2b0f;--bad:#ff8f85;--badbg:#42201d;
--idle:#6b7588;--idlebg:#222834}}
*{box-sizing:border-box}
body{margin:0;padding:20px 24px 40px;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
h1{font-size:20px;margin:0 0 2px}h2{font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);margin:26px 0 8px}
.sub{color:var(--mute);font-size:12.5px}code,.mono{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin-top:14px}
.kpi,.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi .lab{font-size:11.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--mute)}
.kpi .big{font-size:22px;font-weight:650;margin:2px 0}.kpi .big.small{font-size:16px}
.kpi.wait{border-color:var(--wait);background:var(--waitbg)}.kpi.bad{border-color:var(--bad);background:var(--badbg)}
.bar{height:6px;border-radius:3px;background:var(--idlebg);overflow:hidden;margin-top:6px}.bar i{display:block;height:100%;background:var(--ok)}
.banner{margin-top:12px;padding:10px 14px;border-radius:10px;border:1px solid var(--bad);background:var(--badbg);color:var(--bad);font-weight:600}
.banner.wait{border-color:var(--wait);background:var(--waitbg);color:var(--wait)}
.track{display:flex;flex-wrap:wrap;gap:10px}
.part{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 10px 10px}
.part>.lab{font-size:12px;color:var(--mute);margin-bottom:6px}.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{display:block;min-width:92px;padding:6px 9px;border-radius:8px;border:1px solid transparent;text-decoration:none;color:inherit}
.chip b{display:block;font-size:13.5px}.chip span{font-size:11.5px}.chip .bar{height:3px;margin-top:4px;background:rgba(127,127,127,.25)}
.ok{background:var(--okbg);color:var(--ok)}.run{background:var(--runbg);color:var(--run)}.wait{background:var(--waitbg);color:var(--wait)}
.bad{background:var(--badbg);color:var(--bad)}.idle,.skip{background:var(--idlebg);color:var(--idle)}
.chip.run,.step.run{border-color:var(--run)}.chip.wait,.step.wait{border-color:var(--wait)}.chip.bad,.step.bad{border-color:var(--bad)}
.card{margin-bottom:12px}.card h3{margin:0;font-size:16px}.card .head{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:baseline}
.badge{font-size:11.5px;font-weight:650;padding:2px 8px;border-radius:999px}
.steps{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 2px;align-items:stretch}
.grp{display:flex;gap:6px;padding:3px;border:1px dashed var(--mute);border-radius:9px;position:relative}
.step{min-width:74px;padding:5px 8px;border-radius:7px;border:1px solid transparent}
.step .n{font-weight:600;font-size:12.5px}.step .m{display:block;font-size:11px;opacity:.85;min-height:15px}
.step.skip .n{text-decoration:line-through}
.why{margin-top:8px;padding:8px 10px;border-radius:8px}
table{border-collapse:collapse;width:100%;margin-top:8px}th{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mute);text-align:left;font-weight:600}
th,td{padding:5px 10px 5px 0;border-bottom:1px solid var(--line);vertical-align:top}tr:last-child td{border-bottom:0}
.late{color:var(--bad);font-weight:650}a{color:var(--run)}.foot{margin-top:26px;color:var(--mute);font-size:12px}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;color:var(--mute);margin-top:8px}.legend i{font-style:normal;padding:1px 7px;border-radius:6px}
"""

JS = """
(function(){
  function fmt(s){var m=Math.floor(Math.max(0,s)/60);if(m<1)return '<1 min';return m<120?m+' min':Math.floor(m/60)+' h '+(m%60)+' min';}
  function tick(){
    var now=Date.now()/1000;
    document.querySelectorAll('[data-t]').forEach(function(e){
      var t=Date.parse(e.dataset.t)/1000;if(!t)return;
      var s=now-t;
      e.textContent=e.dataset.mode==='for'?fmt(s):(s<60?'just now':fmt(s)+' ago');
      if(e.dataset.warn)e.classList.toggle('late',s>parseFloat(e.dataset.warn));
    });
    var b=document.body,age=now-Date.parse(b.dataset.written)/1000,st=document.getElementById('stale');
    if(st&&b.dataset.alive==='1'&&age>parseFloat(b.dataset.staleAfter)){st.hidden=false;st.querySelector('b').textContent=fmt(age);}
  }
  tick();setInterval(tick,5000);
})();
"""


def esc(x: object) -> str:
    return html.escape(str(x if x is not None else ""), quote=True)


def t_ago(ts: str, now: float, warn: int = 0, never: str = "never") -> str:
    if not epoch(ts):
        return never
    extra = f' data-warn="{warn}"' if warn else ""
    if warn and now - epoch(ts) > warn:
        extra += ' class="late"'
    return f'<span data-t="{esc(ts)}" data-mode="ago"{extra}>{esc(ago(ts, now))}</span>'


def t_for(ts: str, now: float) -> str:
    if not epoch(ts):
        return ""
    return f'<span data-t="{esc(ts)}" data-mode="for">{esc(dur(now - epoch(ts)))}</span>'


def anchor(pr: str) -> str:
    return "pr-" + "".join(c if c.isalnum() else "-" for c in pr)


def step_html(s: dict, now: float) -> str:
    cls = STEP_CLASS.get(s["status"], "bad")
    if s["status"] == "done":
        meta = esc(took(s["started"], s["ended"]) or "passed")
    elif s["status"] == "skipped":
        meta = "skipped"
    elif s["status"] == "pending":
        meta = ""
    elif s["status"] == "blocked":
        meta = "the coordinator"
    elif cls == "run":
        meta = ("starting " if s["status"] == "starting" else "") + t_for(s["started"], now)
    else:
        meta = esc(s["status"].replace("_", " "))
    tip = " · ".join(x for x in (s["who"] or ("script" if s["type"] == "script" else "gate" if s["type"] == "gate" else ""),
                                 f"attempt {s['n']}" if s["n"] > 1 else "", s["note"]) if x)
    again = f" ×{s['n']}" if s["n"] > 1 else ""
    return (f'<div class="step {cls}" title="{esc(tip)}"><span class="n">{STEP_GLYPH.get(s["status"], "✗")} '
            f'{esc(s["id"])}{again}</span><span class="m">{meta}</span></div>')


def steps_html(steps: "list[dict]", now: float) -> str:
    out, i = [], 0
    while i < len(steps):
        g = steps[i]["group"]
        j = i + 1
        while g and j < len(steps) and steps[j]["group"] == g:
            j += 1
        cells = "".join(step_html(s, now) for s in steps[i:j])
        out.append(f'<div class="grp" title="these run at once">{cells}</div>' if j - i > 1 else cells)
        i = j
    return f'<div class="steps">{"".join(out)}</div>'


def workers_table(ws: "list[dict]", now: float, with_pr: bool = False) -> str:
    if not ws:
        return ""
    head = ("<th>PR</th>" if with_pr else "") + ("<th>Runs now</th><th>Agent · model · effort</th><th>Started</th>"
                                                  "<th>Last heartbeat</th><th>It says it is</th><th>Orca dispatch</th>")
    body = []
    for w in ws:
        if w["kind"] == "script":
            cells = [f"<b>{esc(w['step'])}</b>", "a script the router runs: no worker", t_ago(w["started"], now), "-", "-", "-"]
        else:
            name = f"<b>{esc(w['title'])}</b>" + (f" · attempt {w['n']}" if w["n"] > 1 else "")
            beat = t_ago(w["heartbeat"], now, HEARTBEAT_LATE_S, never="none yet") if w["dispatch"] else "starting"
            cells = [name, esc(w["who"] or "not recorded"), t_ago(w["started"], now), beat, esc(w["phase"] or "-"),
                     f'<span class="mono">{esc(w["dispatch"] or "-")}</span>']
        if with_pr:
            cells.insert(0, esc(w["pr"] or "-"))
        body.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return f"<table><tr>{head}</tr>{''.join(body)}</table>"


def card_html(r: dict, d: dict, now: float) -> str:
    cls = ROW_CLASS.get(r["state"], "bad")
    link = f' · <a href="{esc(r["url"])}">{esc(r["url"])}</a>' if r["url"].startswith("http") else ""
    out = [f'<div class="card" id="{anchor(r["id"])}"><div class="head"><h3>{esc(r["id"])}</h3>'
           f'<span class="badge {cls}">{ROW_GLYPH.get(r["state"], "?")} {esc(r["state"])}</span>'
           f'<span>step {min(r["done"] + 1, r["total"])} of {r["total"]}: <b>{esc(at_text(r))}</b></span>'
           f'<span class="sub">{t_for(r["created"], now)} since its chain started{link}</span></div>']
    if r["title"]:
        out.append(f'<div class="sub">{esc(r["title"])}</div>')
    out.append(steps_html(r["steps"], now))
    if r["state"] in WAITING:
        since = f" for {t_for(r['since'], now)}" if epoch(r.get("since", "")) else ""
        out.append(f'<div class="why {cls}"><b>Waiting{since}:</b> {esc(owner_action(r))}'
                   + (f"<br>{esc(r['why'])}" if r["why"] else "") + "</div>")
    out.append(workers_table([w for w in d["workers"] if w["pr"] == r["id"] and w["kind"] != "ad hoc"], now))
    return "".join(out) + "</div>"


def html_page(d: dict) -> str:
    now = time.time()
    rows = d["rows"]
    done = sum(1 for r in rows if r["state"] == "done")
    open_rows = [r for r in rows if r["state"] not in ("done", "not started")]
    waiting = [r for r in rows if r["state"] in WAITING]
    alive = bool(d["mailbox"]["alive"])
    title = d.get("title") or d.get("objective") or "Router run"

    running = [w for w in d["workers"]]
    if running:
        now_big = "<br>".join(f"{esc(w['pr'] or 'ad hoc')} · {esc(w['step'] or w['title'])}" for w in running[:4])
        now_sub = "<br>".join(
            (f"script, no worker · {t_for(w['started'], now)}" if w["kind"] == "script"
             else f"{esc(w['who'] or 'agent not recorded')} · {t_for(w['started'], now)}") for w in running[:4])
    else:
        now_big, now_sub = "nothing", "no worker and no script is running"
    if waiting or d["questions"]:
        wait_big = "<br>".join([f"{esc(r['id'])} · {esc(at_text(r))}" for r in waiting]
                               + [f"{esc(q['pr'] or '-')} · a question" for q in d["questions"]])
        wait_sub = "<br>".join([esc(owner_action(r)) + (f" · {t_for(r['since'], now)}" if epoch(r.get("since", "")) else "")
                                for r in waiting] + [f"asked {t_ago(q['asked'], now)}" for q in d["questions"]])
    else:
        wait_big, wait_sub = "nothing", "no gate, no failure, no question"
    wait_cls = " bad" if any(r["state"] in ("paused", "runner gone") for r in waiting) else " wait" if waiting or d["questions"] else ""
    pct = int(100 * done / len(rows)) if rows else 0
    tab = f"{done}/{len(rows)} · " + (f"◆ {waiting[0]['id']} waits" if waiting else
                                      f"▶ {running[0]['pr']} {running[0]['step'] or running[0]['title']}" if running else "idle")

    o = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="{RELOAD_S}">'
         f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(tab)}</title><style>{CSS}</style></head>'
         f'<body data-written="{esc(d["written"])}" data-alive="{1 if alive else 0}" '
         f'data-stale-after="{int(max(300, 3 * d.get("refresh_s", 120)))}">',
         f'<h1>{esc(title)}</h1><div class="sub">Run <span class="mono">{esc(d.get("run") or "none")}</span>'
         + (f' · {esc(d["objective"])}' if d.get("objective") and d.get("objective") != title else "")
         + (f' · flow v{esc(d["flow"]["version"])}' if d.get("flow") else "")
         + f' · state <span class="mono">{esc(d["state_dir"])}</span></div>']
    if not alive:
        o.append('<div class="banner">The mailbox daemon is not running, so no worker message is being routed and this '
                 'page is not being rewritten. Start it: <code>router.py init</code></div>')
    o.append('<div class="banner" id="stale" hidden>The router last rewrote this page <b></b> ago. It rewrites it at every '
             'change and at least every few minutes, so the mailbox daemon has probably stopped: <code>router.py status</code></div>')
    for g in d["rings"]:
        if now - epoch(g["rang"]) > 300:
            o.append(f'<div class="banner wait">A ring has waited {t_for(g["rang"], now)} and the coordinator has not picked it up. '
                     f'Its <code>router.py wait</code> may not be running. The ring: {esc(g["line"])}</div>')
    o.append('<div class="kpis">'
             f'<div class="kpi"><div class="lab">Inner PRs done</div><div class="big">{done} of {len(rows)}</div>'
             f'<div class="bar"><i style="width:{pct}%"></i></div><div class="sub">{esc(counts(rows)) or "no chain yet"}'
             + ("" if d.get("has_plan") else " · no plan recorded: only started PRs are counted") + "</div></div>"
             f'<div class="kpi"><div class="lab">Running now</div><div class="big small">{now_big}</div><div class="sub">{now_sub}</div></div>'
             f'<div class="kpi{wait_cls}"><div class="lab">Waiting on the coordinator</div><div class="big small">{wait_big}</div>'
             f'<div class="sub">{wait_sub}</div></div>'
             f'<div class="kpi{"" if alive else " bad"}"><div class="lab">Router</div><div class="big small">mailbox daemon '
             f'{"alive" if alive else "NOT RUNNING"}</div><div class="sub">last delivery {t_ago(d["mailbox"]["last"], now)} · '
             f'page written {t_ago(d["written"], now)}</div></div></div>')

    o.append("<h2>The run</h2><div class=\"track\">")
    part, opened = object(), False
    for r in rows:
        if r["part"] != part:
            part = r["part"]
            o.append(("</div></div>" if opened else "") + f'<div class="part"><div class="lab">{esc(part or "inner PRs")}</div><div class="chips">')
            opened = True
        cls = ROW_CLASS.get(r["state"], "bad")
        state = r["state"] if r["state"] in ("done", "not started") else f"{r['state']}: {at_text(r)}"
        if r.get("waits"):
            state += f" · {r['waits']}"
        bar = f'<div class="bar"><i style="width:{int(100 * r["done"] / r["total"])}%"></i></div>' if r["total"] and r["state"] != "done" else ""
        href = f' href="#{anchor(r["id"])}"' if r["state"] not in ("done", "not started") else ""
        o.append(f'<a class="chip {cls}"{href} title="{esc(r["title"])}"><b>{ROW_GLYPH.get(r["state"], "?")} {esc(r["id"])}</b>'
                 f'<span>{esc(state)}</span>{bar}</a>')
    o.append(("</div></div>" if opened else '<div class="sub">No chain has been started.</div>') + "</div>")
    o.append('<div class="legend"><span><i class="ok">✓ done</i></span><span><i class="run">▶ running</i></span>'
             '<span><i class="wait">◆ waits for the coordinator at a gate</i></span><span><i class="bad">✗ failed or stuck</i></span>'
             '<span><i class="idle">· not started</i></span><span><i class="skip">– skipped</i></span></div>')

    if open_rows:
        o.append("<h2>Open now</h2>" + "".join(card_html(r, d, now) for r in open_rows))
    adhoc = [w for w in d["workers"] if w["kind"] == "ad hoc"]
    if adhoc:
        o.append('<h2>Workers outside a chain</h2><div class="card">' + workers_table(adhoc, now, with_pr=True) + "</div>")
    if d["questions"]:
        o.append('<h2>Questions not answered</h2><div class="card"><table><tr><th>PR</th><th>Step</th><th>Asked</th><th>Question</th></tr>'
                 + "".join(f"<tr><td>{esc(q['pr'] or '-')}</td><td>{esc(q['step'] or '-')}</td><td>{t_ago(q['asked'], now)}</td>"
                           f"<td>{esc(q['text'])}</td></tr>" for q in d["questions"]) + "</table></div>")
    finished = [r for r in rows if r["state"] == "done"]
    if finished:
        o.append('<h2>Done</h2><div class="card"><table><tr><th>PR</th><th>What</th><th>Took</th><th>Steps run</th><th>Second attempts</th><th>Link</th></tr>')
        for r in finished:
            ran = sum(1 for s in r["steps"] if s["status"] == "done")
            again = ", ".join(f"{s['id']} ×{s['n']}" for s in r["steps"] if s["n"] > 1) or "none"
            link = f'<a href="{esc(r["url"])}">{esc(r["url"])}</a>' if r["url"].startswith("http") else "-"
            o.append(f"<tr><td><b>{esc(r['id'])}</b></td><td>{esc(r['title'])}</td><td>{esc(took(r['created'], r['ended']) or '?')}</td>"
                     f"<td>{ran} of {r['total']}</td><td>{esc(again)}</td><td>{link}</td></tr>")
        o.append("</table></div>")
    if d["events"]:
        o.append('<h2>Last events</h2><div class="card"><table><tr><th>When</th><th>PR</th><th>Step</th><th>What</th></tr>'
                 + "".join(f"<tr><td>{esc(clock(e['t']))}</td><td>{esc(e['pr'])}</td><td>{esc(e['step'])}</td><td>{esc(e['text'])}</td></tr>"
                           for e in reversed(d["events"])) + "</table></div>")
    o.append(f'<div class="foot">Written by <code>router.py</code> at {esc(d["written"])}. The page reloads itself every {RELOAD_S} s. '
             f'In a terminal: <code>router.py progress</code>. Every event: <span class="mono">{esc(d["state_dir"])}/journal.md</span></div>'
             f"<script>{JS}</script></body></html>")
    return "".join(o)
