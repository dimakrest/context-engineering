/* The router's page: the live view of a run and its flow, and edits to the flow on the same canvas.

   View mode draws GET /state (router.py's progress_data plus the flow) every 5 s: what progress.html shows, with the
   PRs as a graph, one lane per part and an edge for each "after". Edit mode keeps a local copy of the flow (GET /flow)
   and changes only that copy until Apply. Apply sends the copy to POST /flow twice: a dry run, whose change lines are
   the ones the router will write in the flow's history, then the apply itself. The canvas is drawn again from
   GET /state and GET /flow after every apply: nothing is assumed to have worked.

   What the router refuses is drawn locked: a step up to a chain's last settled step cannot move, go away or have
   a step put before it; a started PR cannot be dropped. The router checks again on Apply and has the last word. */
(function () {
  'use strict';
  var POLL_MS = 5000, STALE_S = 30, HEARTBEAT_LATE_S = 600, RING_LATE_S = 300;
  var STEP_GLYPH = {done: '✓', skipped: '–', running: '▶', starting: '▶', script: '▶', recheck: '▶', blocked: '◆', pending: '·'};
  var STEP_CLASS = {done: 'ok', skipped: 'skip', running: 'run', starting: 'run', script: 'run', recheck: 'run', blocked: 'wait', pending: 'idle'};
  var ROW_GLYPH = {'done': '✓', 'running': '▶', 'at a gate': '◆', 'paused': '✗', 'stopped': '■', 'runner gone': '✗', 'not started': '·'};
  var ROW_CLASS = {'done': 'ok', 'running': 'run', 'at a gate': 'wait', 'paused': 'bad', 'stopped': 'wait', 'runner gone': 'bad', 'not started': 'idle'};
  var WAITING = ['at a gate', 'paused', 'stopped', 'runner gone'];
  var ROUTER_OWN = ['version', 'history', 'dir', 'resolved', 'removed'];   // keys only the router writes in its copy
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  var S = {
    data: null,       // the last GET /state
    lastOk: 0,        // when it arrived (ms)
    since: Date.now(),
    edit: false,
    copy: null,       // GET /flow: the router's copy the edits started from
    draft: null,      // the local flow: the copy without the router's own keys, edited
    problems: [],     // the router's refusal of the last Apply (422)
    review: null,     // {base, changes}: the dry run's lines, waiting for the owner's yes
    notice: null,     // {cls, text}: one line about the last action
    form: null,       // {kind: 'step', pid, sid} | {kind: 'pr', after}
    drag: null,
    busy: false,
    held: false       // a poll arrived while a form, a review or a drag was open: draw it when that closes
  };

  // ------------------------------------------------------------ small helpers

  function h(tag, props) {
    var e = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      var v = props[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k.slice(0, 2) === 'on') listen(e, k.slice(2), v);
      else if (k === 'dataset') Object.keys(v).forEach(function (d) { if (v[d] !== undefined && v[d] !== null) e.dataset[d] = v[d]; });
      else if (typeof v === 'boolean' || k === 'value') e[k] = v;
      else e.setAttribute(k, v);
    });
    for (var i = 2; i < arguments.length; i++) put(e, arguments[i]);
    return e;
  }
  function put(e, c) {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) c.forEach(function (x) { put(e, x); });
    else e.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
  }
  // An element's handlers live in e._on, behind one listener per event type: when a redraw keeps the element, it
  // takes the new drawing's handlers (patch), so no handler closes over a draw that is gone.
  function listen(e, type, f) {
    if (!e._on) e._on = {};
    if (!(type in e._on)) e.addEventListener(type, function (ev) { var g = e._on[type]; return g ? g(ev) : undefined; });
    e._on[type] = f;
  }
  function clone(x) { return JSON.parse(JSON.stringify(x)); }
  function lc(x) { return String(x || '').toLowerCase(); }
  function epoch(ts) { var t = Date.parse(ts || ''); return isNaN(t) ? 0 : t / 1000; }
  function nowS() { return Date.now() / 1000; }
  function dur(s) {
    var m = Math.floor(Math.max(0, s) / 60);
    if (m < 1) return '<1 min';
    return m < 120 ? m + ' min' : Math.floor(m / 60) + ' h ' + (m % 60) + ' min';
  }
  function ago(ts) { if (!epoch(ts)) return 'never'; var s = nowS() - epoch(ts); return s < 60 ? 'just now' : dur(s) + ' ago'; }
  function took(a, b) { return epoch(a) && epoch(b) ? dur(epoch(b) - epoch(a)) : ''; }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function clock(ts) {
    var e = epoch(ts); if (!e) return '';
    var d = new Date(e * 1000);
    return pad(d.getDate()) + ' ' + MONTHS[d.getMonth()] + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }
  // A time that keeps itself current between polls (tick): "3 min ago", or "for 3 min".
  function tAgo(ts, warn, never) {
    if (!epoch(ts)) return never || 'never';
    var late = warn && nowS() - epoch(ts) > warn;
    return h('span', {dataset: {t: ts, mode: 'ago', warn: warn ? String(warn) : undefined}, class: late ? 'late' : null}, ago(ts));
  }
  function tFor(ts) { return epoch(ts) ? h('span', {dataset: {t: ts, mode: 'for'}}, dur(nowS() - epoch(ts))) : ''; }
  function anchor(pr) { return 'pr-' + String(pr).replace(/[^A-Za-z0-9]/g, '-'); }
  function atText(r) { return (r.at || []).join(' ∥ ') || '-'; }
  function ownerAction(r) {
    if (r.state === 'runner gone') return 'its runner is gone: router.py resume ' + r.id;
    if (r.state === 'stopped') return 'stopped with router.py stop: router.py resume ' + r.id;
    return 'the coordinator decides' + (r.picked_up === false ? '; it has NOT picked the ring up yet' : '');
  }
  function counts(rows) {
    return ['done', 'running', 'at a gate', 'paused', 'stopped', 'runner gone', 'not started'].map(function (k) {
      var n = rows.filter(function (r) { return r.state === k; }).length;
      return n ? n + ' ' + k : '';
    }).filter(Boolean).join(' · ');
  }
  function who(d) { return ['agent', 'model', 'effort'].map(function (k) { return d[k] || ''; }).filter(Boolean).join(' '); }

  // ------------------------------------------------------------ talking to the router

  function getJSON(path) {
    return fetch(path, {cache: 'no-store'}).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.reason || ('HTTP ' + r.status)); return j; });
    });
  }

  // The page is drawn again only when the run changed: a DOM that stays put keeps the reader's place, a screen
  // reader's focus, and the element refs of Orca's browser automation (orca snapshot, then orca click or drag).
  function sameRun(a, b) {
    var strip = function (d) { return JSON.stringify(d, function (k, v) { return k === 'written' || k === 'last' ? undefined : v; }); };
    return !!a && strip(a) === strip(b);
  }

  function poll() {
    var same = false;
    return getJSON('/state').then(function (d) {
      same = sameRun(S.data, d);
      S.data = d; S.lastOk = Date.now();
      var last = document.getElementById('last-delivery');
      if (last && d.mailbox.last) last.dataset.t = d.mailbox.last;   // ticks on without a redraw
      if (S.edit && d.flow && S.copy && d.flow.version !== S.copy.version && !dirty()) { same = false; return loadFlow(); }
    }).then(function () {
      if (busy()) { if (!same) S.held = true; tick(); } else if (same) tick(); else draw();
    }, function () { tick(); });
  }

  function loadFlow() {
    return getJSON('/flow').then(function (f) {
      S.copy = f;
      S.draft = userPart(clone(f));
    });
  }

  function post(body) {
    return fetch('/flow', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
      .then(function (r) { return r.json().then(function (j) { return {status: r.status, body: j}; }); });
  }

  // A poll does not redraw under the owner's hands: a drag, an open form or review, a field being typed in.
  function busy() {
    var a = document.activeElement;
    return !!(S.drag || S.form || S.review || (a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName)));
  }

  // ------------------------------------------------------------ the draft

  function userPart(f) {
    var o = {};
    Object.keys(f).forEach(function (k) { if (ROUTER_OWN.indexOf(k) < 0) o[k] = f[k]; });
    return o;
  }
  function dirty() { return !!(S.draft && S.copy && JSON.stringify(S.draft) !== JSON.stringify(userPart(S.copy))); }
  function serverPr(pid) {
    var prs = (S.data && S.data.flow && S.data.flow.prs) || [];
    for (var i = 0; i < prs.length; i++) if (lc(prs[i].id) === lc(pid)) return prs[i];
    return null;
  }
  // How many of a PR's first steps are fixed: up to its chain's last settled step (0 for a PR not started).
  function fixedOf(pid) { var p = serverPr(pid); return p && p.started ? p.fixed : 0; }
  function draftPr(pid) {
    var prs = S.draft.prs || [];
    for (var i = 0; i < prs.length; i++) if (lc(prs[i].id) === lc(pid)) return prs[i];
    return null;
  }
  function stepsOf(p) {
    if (Array.isArray(p.steps)) return p.steps;
    var t = ((S.copy && S.copy.resolved && S.copy.resolved.templates) || {})[p.template];
    return t ? t.def.steps : [];
  }
  function ownSteps(p) { if (!Array.isArray(p.steps)) p.steps = clone(stepsOf(p)); return p.steps; }
  function freeId(steps, base) {
    var id = base, n = 2;
    while (steps.some(function (s) { return s.id === id; })) id = base + '_' + (n++);
    return id;
  }
  function stepIndex(steps, sid) { for (var i = 0; i < steps.length; i++) if (steps[i].id === sid) return i; return -1; }

  // Whether a drop at index of PR pid is allowed, and if not, why (the reason shown on the canvas).
  function dropRefusal(drag, t) {
    if (!t) return 'not a place for a step';
    var p = draftPr(t.pid);
    if (!p) return 'that PR is not in the flow';
    // After the last settled step is the start of the pending steps, a place like any other, even when none is left.
    var k = fixedOf(t.pid);
    if (t.index < k && t.locked) return 'step ' + t.sid + ' is ' + t.status + ': a settled step cannot move and no step can go before it';
    if (t.index < k) return 'the steps of ' + p.id + ' up to its last settled step are fixed';
    if (drag.kind === 'step') {
      if (lc(drag.pid) !== lc(t.pid)) return 'a step moves within its own PR';
      if (stepIndex(stepsOf(p), drag.sid) < k) return 'step ' + drag.sid + ' is settled';
    }
    return '';
  }
  function drop(drag, t) {
    var steps = ownSteps(draftPr(t.pid)), index = t.index;
    if (drag.kind === 'step') {
      var from = stepIndex(steps, drag.sid);
      if (index === from || index === from + 1) return false;
      var s = steps.splice(from, 1)[0];
      steps.splice(from < index ? index - 1 : index, 0, s);
    } else {
      var d = clone(drag.step);
      d.id = freeId(steps, d.id);
      steps.splice(index, 0, d);
    }
    return true;
  }
  function edited() { S.problems = []; S.review = null; S.notice = null; }

  // ------------------------------------------------------------ drawing

  function draw() {
    var app = document.getElementById('app');
    var d = S.data;
    S.held = false;
    if (!d) { tick(); return; }
    var keep = window.scrollY, gw = document.querySelector('.graph-wrap'), gx = gw ? gw.scrollLeft : 0;
    var next = h('div', {id: 'app'});
    put(next, [header(d), banners(d), kpis(d)]);
    put(next, h('h2', {}, S.edit ? 'The flow, editing' : 'The run'));
    if (S.edit) put(next, editTools(d));
    put(next, h('div', {class: 'graph-wrap'}, graph(d)));
    put(next, legend());
    put(next, rest(d));
    patch(app, next);
    document.title = tabTitle(d);
    edges();
    window.scrollTo(0, keep);
    gw = document.querySelector('.graph-wrap'); if (gw) gw.scrollLeft = gx;
    tick();
  }

  // patch: the page as drawn becomes the new drawing, in place. A PR (its id), a step chip (its PR and step) and a
  // palette role are the same elements from one draw to the next wherever they move, and so is every other element
  // whose place and tag did not change: only what changed is added, removed or rewritten. A click or a drag that
  // began on an element, and the refs Orca's browser automation took of it, outlive the poll that redraws the page.
  // Form fields are drawn afresh: a draw never comes while one is in use (busy).
  function keyOf(e) {
    if (e.nodeType !== 1) return null;
    if (e.id) return '#' + e.id;
    if (e.dataset.step !== undefined && e.dataset.pr !== undefined) return 'step:' + e.dataset.pr + ':' + e.dataset.step;
    if (e.dataset.drag === 'role') return 'role:' + e.dataset.name;
    return null;
  }
  function patch(app, next) {
    var keyed = {};
    app.querySelectorAll('[id],[data-step],[data-drag]').forEach(function (e) { var k = keyOf(e); if (k && !keyed[k]) keyed[k] = e; });
    var used = typeof WeakSet === 'function' ? new WeakSet() : null;
    function same(a, b) {
      return a.nodeType === b.nodeType && a.nodeName === b.nodeName && !/^(INPUT|SELECT|TEXTAREA)$/.test(a.nodeName) && (!used || !used.has(a));
    }
    function morph(a, b) {   // a, on the page, takes b's attributes, handlers and children
      if (used) used.add(a);
      if (a.nodeType !== 1) { if (a.nodeValue !== b.nodeValue) a.nodeValue = b.nodeValue; return; }
      Array.prototype.slice.call(a.attributes).forEach(function (x) { if (!b.hasAttribute(x.name)) a.removeAttribute(x.name); });
      Array.prototype.slice.call(b.attributes).forEach(function (x) { if (a.getAttribute(x.name) !== x.value) a.setAttribute(x.name, x.value); });
      if (a._on) Object.keys(a._on).forEach(function (t) { if (!b._on || !(t in b._on)) a._on[t] = null; });
      if (b._on) Object.keys(b._on).forEach(function (t) { listen(a, t, b._on[t]); });
      children(a, b);
    }
    function children(a, b) {
      var olds = Array.prototype.slice.call(a.childNodes), free = olds.filter(function (o) { return !keyOf(o); }), at = 0;
      Array.prototype.slice.call(b.childNodes).forEach(function (c) {
        var k = keyOf(c), o = null;
        if (k) o = keyed[k] && same(keyed[k], c) ? keyed[k] : null;
        else {
          for (var j = 0; j < free.length; j++) if (free[j] && same(free[j], c)) { o = free[j]; free[j] = null; break; }
        }
        var here = a.childNodes[at], el = o || c;
        if (here !== el) a.insertBefore(el, here || null);   // moved first: what morph takes is never its ancestor
        if (o) morph(o, c); else adopt(c);
        at++;
      });
      while (a.childNodes.length > at) a.removeChild(a.lastChild);
    }
    function adopt(c) {   // a new element: any keyed element inside it that the page already has is kept
      Array.prototype.slice.call(c.childNodes).forEach(function (x) {
        var k = keyOf(x), o = k && keyed[k] && same(keyed[k], x) ? keyed[k] : null;
        if (o) { c.replaceChild(o, x); morph(o, x); } else adopt(x);
      });
    }
    morph(app, next);
  }

  function tabTitle(d) {
    var rows = d.rows, done = rows.filter(function (r) { return r.state === 'done'; }).length;
    var waiting = rows.filter(function (r) { return WAITING.indexOf(r.state) >= 0; });
    var w = d.workers[0];
    return (S.edit && dirty() ? '✎ ' : '') + done + '/' + rows.length + ' · ' +
      (waiting.length ? '◆ ' + waiting[0].id + ' waits' : w ? '▶ ' + w.pr + ' ' + (w.step || w.title) : 'idle');
  }

  function header(d) {
    var title = (d.flow && d.flow.title) || d.title || d.objective || 'Router run';
    var flow = d.flow;
    var sub = [h('span', {}, 'Run '), h('span', {class: 'mono'}, d.run || 'none')];
    if (d.objective && d.objective !== title) sub.push(' · ' + d.objective);
    if (flow) {
      var used = d.rows.filter(function (r) { return ['done', 'not started'].indexOf(r.state) < 0; }).length;
      sub.push(' · flow v' + flow.version + ' · slots ' + used + '/' + flow.slots + ' · start ' + flow.start);
    }
    sub.push(' · state ', h('span', {class: 'mono'}, d.state_dir));
    sub.push(' · refreshed ', h('span', {id: 'refreshed'}, 'just now'), ', every ' + POLL_MS / 1000 + ' s');
    var mode = h('div', {class: 'mode', role: 'group', 'aria-label': 'mode'},
      h('button', {type: 'button', 'aria-pressed': String(!S.edit), onclick: function () { setEdit(false); }}, 'View'),
      h('button', {type: 'button', 'aria-pressed': String(S.edit), disabled: !flow, id: 'edit-toggle',
                   title: flow ? 'edit the flow on the canvas' : 'no flow: router.py flow apply <file>',
                   onclick: function () { setEdit(true); }}, 'Edit the flow'));
    return h('div', {class: 'top'}, h('div', {}, h('h1', {}, title), h('div', {class: 'sub'}, sub)), mode);
  }

  function banners(d) {
    var out = [];
    if (!d.mailbox.alive) out.push(h('div', {class: 'banner'}, 'The mailbox daemon is not running, so no worker message is being routed. Start it: ', h('code', {}, 'router.py init')));
    out.push(h('div', {class: 'banner', id: 'stale', hidden: true}, 'The page has not heard from the router for ', h('b', {}, ''),
      '. The mailbox daemon serves it, so the daemon has probably stopped: ', h('code', {}, 'router.py status'), '. What you see is as it was then.'));
    d.rings.forEach(function (g) {
      if (nowS() - epoch(g.rang) > RING_LATE_S)
        out.push(h('div', {class: 'banner wait'}, 'A ring has waited ', tFor(g.rang), ' and the coordinator has not picked it up. Its ',
          h('code', {}, 'router.py wait'), ' may not be running. The ring: ' + g.line));
    });
    if (S.edit && S.copy && d.flow && d.flow.version !== S.copy.version && dirty())
      out.push(h('div', {class: 'banner wait'}, 'The flow is now v' + d.flow.version + ' and your edits started from v' + S.copy.version +
        ': Apply will be refused. Discard them to edit v' + d.flow.version + '.'));
    if (S.notice) out.push(h('div', {class: 'banner ' + (S.notice.cls || 'note'), id: 'notice'}, S.notice.text));
    return out;
  }

  function kpis(d) {
    var rows = d.rows, done = rows.filter(function (r) { return r.state === 'done'; }).length;
    var waiting = rows.filter(function (r) { return WAITING.indexOf(r.state) >= 0; });
    var pct = rows.length ? Math.floor(100 * done / rows.length) : 0;
    var run = d.workers.slice(0, 4);
    var runBig = run.length ? run.map(function (w) { return h('div', {}, (w.pr || 'ad hoc') + ' · ' + (w.step || w.title)); }) : 'nothing';
    var runSub = run.length ? run.map(function (w) {
      return h('div', {}, w.kind === 'script' ? 'script, no worker · ' : (w.who || 'agent not recorded') + ' · ', tFor(w.started));
    }) : 'no worker and no script is running';
    var waitBig = waiting.map(function (r) { return h('div', {}, r.id + ' · ' + atText(r)); })
      .concat(d.questions.map(function (q) { return h('div', {}, (q.pr || '-') + ' · a question'); }));
    var waitSub = waiting.map(function (r) { return h('div', {}, ownerAction(r), epoch(r.since) ? [' · ', tFor(r.since)] : null); })
      .concat(d.questions.map(function (q) { return h('div', {}, 'asked ', tAgo(q.asked)); }));
    var waitCls = waiting.some(function (r) { return r.state === 'paused' || r.state === 'runner gone'; }) ? ' bad' : waitBig.length ? ' wait' : '';
    return h('div', {class: 'kpis'},
      h('div', {class: 'kpi'}, h('div', {class: 'lab'}, 'Inner PRs done'), h('div', {class: 'big'}, done + ' of ' + rows.length),
        h('div', {class: 'bar'}, h('i', {style: 'width:' + pct + '%'})),
        h('div', {class: 'sub'}, counts(rows) || 'no chain yet', d.has_plan ? '' : ' · no plan recorded: only started PRs are counted')),
      h('div', {class: 'kpi'}, h('div', {class: 'lab'}, 'Running now'), h('div', {class: 'big small'}, runBig), h('div', {class: 'sub'}, runSub)),
      h('div', {class: 'kpi' + waitCls}, h('div', {class: 'lab'}, 'Waiting on the coordinator'),
        h('div', {class: 'big small'}, waitBig.length ? waitBig : 'nothing'),
        h('div', {class: 'sub'}, waitSub.length ? waitSub : 'no gate, no failure, no question')),
      h('div', {class: 'kpi' + (d.mailbox.alive ? '' : ' bad')}, h('div', {class: 'lab'}, 'Router'),
        h('div', {class: 'big small'}, 'mailbox daemon ' + (d.mailbox.alive ? 'alive' : 'NOT RUNNING')),
        h('div', {class: 'sub'}, 'last delivery ', h('span', {id: 'last-delivery', dataset: {t: d.mailbox.last || undefined, mode: 'ago'}}, ago(d.mailbox.last)))));
  }

  // The PRs to draw, in the flow's order (the draft's in edit mode), then the chains the flow or plan does not name.
  function nodes(d) {
    var rows = {}, out = [];
    d.rows.forEach(function (r) { rows[lc(r.id)] = r; });
    var prs = S.edit && S.draft ? S.draft.prs || [] : d.flow ? d.flow.prs : null;
    if (prs) {
      prs.forEach(function (p) {
        var r = rows[lc(p.id)];
        delete rows[lc(p.id)];
        out.push({id: p.id, part: p.part || '', title: p.title || (r && r.title) || '', after: p.after || [], row: r || null, pr: p, flow: true});
      });
    }
    d.rows.forEach(function (r) {
      if (rows[lc(r.id)]) out.push({id: r.id, part: r.part || '', title: r.title, after: [], row: r, pr: null, flow: false});
    });
    return out;
  }

  function graph(d) {
    var ns = nodes(d), parts = [], depth = {}, byId = {};
    ns.forEach(function (n) { byId[lc(n.id)] = n; if (parts.indexOf(n.part) < 0) parts.push(n.part); });
    function deep(n, seen) {
      if (depth[lc(n.id)] !== undefined) return depth[lc(n.id)];
      if (seen.indexOf(lc(n.id)) >= 0) return 0;   // a cycle: the router refuses it, the canvas just draws it
      var m = 0;
      n.after.forEach(function (a) { var q = byId[lc(a)]; if (q) m = Math.max(m, deep(q, seen.concat([lc(n.id)])) + 1); });
      return (depth[lc(n.id)] = m);
    }
    ns.forEach(function (n) { deep(n, []); });
    var cols = Math.max.apply(null, [0].concat(ns.map(function (n) { return depth[lc(n.id)]; }))) + 1;
    var g = h('div', {class: 'graph', id: 'graph', style: 'grid-template-columns:max-content repeat(' + cols + ',max-content)'});
    if (!ns.length) put(g, h('div', {class: 'sub', style: 'padding:10px'}, d.flow ? 'The flow has no PR.' : 'No chain has been started and no plan or flow is recorded.'));
    parts.forEach(function (part, i) {
      var row = 'grid-row:' + (i + 1);
      put(g, h('div', {class: 'lane', style: row}));
      put(g, h('div', {class: 'lane-lab', style: row + ';grid-column:1'}, part || 'inner PRs'));
      for (var c = 0; c < cols; c++) {
        var here = ns.filter(function (n) { return n.part === part && depth[lc(n.id)] === c; });
        if (here.length) put(g, h('div', {class: 'cell', style: row + ';grid-column:' + (c + 2)}, here.map(node)));
      }
    });
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'edges');
    svg.dataset.edges = JSON.stringify([].concat.apply([], ns.map(function (n) {
      return n.after.filter(function (a) { return byId[lc(a)]; }).map(function (a) { return [byId[lc(a)].id, n.id]; });
    })));
    put(g, svg);
    return g;
  }

  // Each "after" as a curve from the right edge of the PR it waits for to the left edge of the waiting PR.
  function edges() {
    var g = document.getElementById('graph'); if (!g) return;
    var svg = g.querySelector('svg.edges'), base = g.getBoundingClientRect();
    svg.setAttribute('width', g.scrollWidth); svg.setAttribute('height', g.scrollHeight);
    svg.textContent = '';
    var ns = 'http://www.w3.org/2000/svg';
    var defs = document.createElementNS(ns, 'defs'), mk = document.createElementNS(ns, 'marker');
    mk.setAttribute('id', 'arrow'); mk.setAttribute('viewBox', '0 0 8 8'); mk.setAttribute('refX', '7'); mk.setAttribute('refY', '4');
    mk.setAttribute('markerWidth', '7'); mk.setAttribute('markerHeight', '7'); mk.setAttribute('orient', 'auto');
    var tip = document.createElementNS(ns, 'path'); tip.setAttribute('d', 'M0,0 L8,4 L0,8 z'); tip.setAttribute('fill', 'currentColor');
    mk.appendChild(tip); defs.appendChild(mk); svg.appendChild(defs);
    JSON.parse(svg.dataset.edges || '[]').forEach(function (e) {
      var a = document.getElementById(anchor(e[0])), b = document.getElementById(anchor(e[1]));
      if (!a || !b) return;
      var ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      var x1 = ra.right - base.left, y1 = ra.top + Math.min(ra.height / 2, 40) - base.top;
      var x2 = rb.left - base.left - 2, y2 = rb.top + Math.min(rb.height / 2, 40) - base.top;
      var dx = Math.max(24, (x2 - x1) / 2);
      var p = document.createElementNS(ns, 'path');
      p.setAttribute('d', 'M' + x1 + ',' + y1 + ' C' + (x1 + dx) + ',' + y1 + ' ' + (x2 - dx) + ',' + y2 + ' ' + x2 + ',' + y2);
      p.setAttribute('marker-end', 'url(#arrow)');
      p.dataset.from = e[0]; p.dataset.to = e[1];
      svg.appendChild(p);
    });
  }

  function node(n) {
    var r = n.row, d = S.data;
    var state = r ? r.state : 'not started', cls = ROW_CLASS[state] || 'bad';
    var isNew = S.edit && n.flow && !serverPr(n.id);
    var meta;
    if (isNew) meta = 'new: not applied yet';
    else if (state === 'done') meta = 'took ' + (took(r.created, r.ended) || '?');
    else if (state === 'not started') meta = (r && r.waits) || (n.flow ? 'not started' : '');
    else meta = ['step ' + Math.min(r.done + 1, r.total) + ' of ' + r.total + ': ', h('b', {}, atText(r)), ' · ', tFor(r.created), ' so far'];
    var link = r && /^https?:/.test(r.url) ? h('a', {href: r.url, target: '_blank', rel: 'noopener'}, 'PR') : null;
    var out = h('div', {class: 'node ' + cls + (isNew ? ' new' : ''), id: anchor(n.id), dataset: {pr: n.id}},
      h('div', {class: 'nhead'}, h('b', {class: 'nid'}, n.id), h('span', {class: 'badge ' + cls}, (ROW_GLYPH[state] || '?') + ' ' + state), link),
      n.title ? h('div', {class: 'ntitle'}, n.title) : null,
      meta ? h('div', {class: 'nmeta'}, meta) : null,
      stepsEl(n));
    if (r && WAITING.indexOf(state) >= 0)
      put(out, h('div', {class: 'why ' + cls}, h('b', {}, 'Waiting', epoch(r.since) ? [' for ', tFor(r.since)] : null, ': '), ownerAction(r), r.why ? [h('br'), r.why] : null));
    d.workers.filter(function (w) { return w.pr === n.id && w.kind !== 'ad hoc'; }).forEach(function (w) { put(out, workerLine(w)); });
    if (S.edit && n.flow) {
      var started = !!(serverPr(n.id) || {}).started;
      put(out, h('div', {class: 'nfoot'},
        h('button', {type: 'button', class: 'btn small', onclick: function () { S.form = {kind: 'pr', after: n.id}; draw(); }}, '+ PR after ' + n.id),
        h('button', {type: 'button', class: 'btn small', disabled: started, 'data-act': 'drop-pr',
                     title: started ? 'its chain exists, so it stays in the flow' : 'take this PR out of the flow',
                     onclick: function () { dropPr(n.id); }}, started ? '🔒 started' : 'Drop PR')));
    }
    return out;
  }

  function workerLine(w) {
    if (w.kind === 'script')
      return h('div', {class: 'wline'}, '▶ ', h('b', {}, w.step), ' · a script the router runs, no worker · started ', tAgo(w.started));
    return h('div', {class: 'wline', title: w.dispatch || ''}, '▶ ', h('b', {}, w.step || w.title), ' · ', w.who || 'agent not recorded',
      w.n > 1 ? ' · attempt ' + w.n : '',
      w.dispatch ? [' · heartbeat ', tAgo(w.heartbeat, HEARTBEAT_LATE_S, 'none yet'), w.phase ? ' (' + w.phase + ')' : ''] : [' · starting since ', tAgo(w.started)]);
  }

  // A PR's steps as chips: what its chain ran (view), or the flow's steps with the chain's state (edit, a PR not started).
  function stepsEl(n) {
    var r = n.row, status = {}, views = {};
    ((r && r.steps) || []).forEach(function (s) { status[s.id] = s.status; views[s.id] = s; });
    var list, editable = S.edit && n.flow;
    if (editable) list = stepsOf(n.pr).map(function (s) { return {def: s, view: views[s.id]}; });
    else if (r && r.steps && r.steps.length) list = r.steps.map(function (s) { return {def: null, view: s}; });
    else {
      var sp = serverPr(n.id);
      list = ((sp && sp.steps) || []).map(function (s) { return {def: s, view: null}; });
    }
    var k = editable ? fixedOf(n.id) : 0;
    var box = h('div', {class: 'steps', dataset: {steps: n.id, n: String(list.length)}});
    var i = 0;
    while (i < list.length) {
      var g = group(list[i]), j = i + 1;
      while (g && j < list.length && group(list[j]) === g) j++;
      var chips = [];
      for (var x = i; x < j; x++) chips.push(chip(n, list[x], x, x < k));
      put(box, j - i > 1 ? h('div', {class: 'grp', title: 'these run at once'}, chips) : chips);
      i = j;
    }
    if (!list.length) put(box, h('span', {class: 'sub'}, editable ? 'no step: drop a role here' : 'no step'));
    return box;
  }
  function group(item) { return (item.def && item.def.group) || (item.view && item.view.group) || ''; }

  function chip(n, item, idx, locked) {
    var v = item.view, def = item.def, id = def ? def.id : v.id;
    var st = v ? v.status : 'pending', cls = STEP_CLASS[st] || 'bad', times = v && v.n > 1 ? ' ×' + v.n : '';
    var tip = [v ? v.who : def ? (def.type === 'gate' ? 'gate: ' + (def.title || '') : def.type === 'script' ? 'script: ' + (def.run || '') : who(def)) : '',
               v && v.n > 1 ? 'attempt ' + v.n : '', v ? v.note : '',
               v && st === 'done' ? 'took ' + (took(v.started, v.ended) || '?') : '',
               v && STEP_CLASS[st] === 'run' && epoch(v.started) ? 'running for ' + dur(nowS() - epoch(v.started)) : ''].filter(Boolean).join(' · ');
    var editable = S.edit && n.flow;
    var sp = serverPr(n.id), wasDef = null;
    if (editable && sp && sp.steps) sp.steps.forEach(function (s) { if (s.id === id) wasDef = s; });
    var changed = editable && def && (!wasDef || JSON.stringify(wasDef) !== JSON.stringify(def));
    var c = h('div', {
      class: 'sc ' + cls + (editable ? locked ? ' locked' : ' drag' : '') + (changed ? ' edited' : ''),
      title: (locked ? 'locked: ' + (st === 'pending' ? 'before a settled step' : st) + ' · ' : '') + tip,
      // a button to assistive tools and to Orca's browser automation (orca snapshot gives it a ref, orca drag uses it)
      role: editable ? 'button' : null, 'aria-disabled': editable && locked ? 'true' : null,
      'aria-label': editable ? 'step ' + id + ' of ' + n.id + ', ' + st + (locked ? ', locked' : '') : null,
      dataset: {step: id, pr: n.id, idx: String(idx), status: st, locked: locked ? '1' : undefined, drag: editable && !locked ? 'step' : undefined}
    }, (STEP_GLYPH[st] || '✗') + ' ' + id + times);
    if (editable && locked) put(c, h('span', {class: 'lock', 'aria-label': 'locked'}, '🔒'));
    if (editable && !locked)
      put(c, h('button', {type: 'button', class: 'x', title: 'remove ' + id, 'aria-label': 'remove ' + id,
                          onclick: function (e) { e.stopPropagation(); removeStep(n.id, id); }}, '×'));
    return c;
  }

  function legend() {
    return h('div', {class: 'legend'}, [['ok', '✓ done'], ['run', '▶ running'], ['wait', '◆ waits for the coordinator at a gate'],
      ['bad', '✗ failed or stuck'], ['idle', '· not started or pending'], ['skip', '– skipped']].map(function (x) {
        return h('span', {}, h('i', {class: x[0]}, x[1]));
      }), S.edit ? h('span', {}, '🔒 locked: the router will not change it') : null,
      h('span', {}, '→ an arrow: the PR waits for the one it comes from (after)'));
  }

  function rest(d) {
    var out = [];
    if (d.workers.length) {
      out.push(h('h2', {}, 'Running now'), h('div', {class: 'card'}, h('table', {},
        h('tr', {}, ['PR', 'Runs now', 'Agent · model · effort', 'Started', 'Last heartbeat', 'It says it is', 'Orca dispatch'].map(function (x) { return h('th', {}, x); })),
        d.workers.map(function (w) {
          var script = w.kind === 'script';
          return h('tr', {}, h('td', {}, w.kind === 'ad hoc' ? (w.pr || '-') + ' (ad hoc)' : w.pr || '-'),
            h('td', {}, h('b', {}, script ? w.step : w.title), w.n > 1 ? ' · attempt ' + w.n : ''),
            h('td', {}, script ? 'a script the router runs: no worker' : w.who || 'not recorded'), h('td', {}, tAgo(w.started)),
            h('td', {}, script ? '-' : w.dispatch ? tAgo(w.heartbeat, HEARTBEAT_LATE_S, 'none yet') : 'starting'),
            h('td', {}, script ? '-' : w.phase || '-'), h('td', {}, h('span', {class: 'mono'}, script ? '-' : w.dispatch || '-')));
        }))));
    }
    if (d.rings.length) {
      out.push(h('h2', {}, 'Rings the coordinator has not picked up'), h('div', {class: 'card'}, h('table', {},
        h('tr', {}, h('th', {}, 'Rang'), h('th', {}, 'PR'), h('th', {}, 'The ring')),
        d.rings.map(function (g) { return h('tr', {}, h('td', {}, tAgo(g.rang, RING_LATE_S)), h('td', {}, g.pr), h('td', {}, g.line)); }))));
    }
    if (d.questions.length) {
      out.push(h('h2', {}, 'Questions not answered'), h('div', {class: 'card'}, h('table', {},
        h('tr', {}, ['PR', 'Step', 'Asked', 'Question', 'Answer with'].map(function (x) { return h('th', {}, x); })),
        d.questions.map(function (q) {
          return h('tr', {}, h('td', {}, q.pr || '-'), h('td', {}, q.step || '-'), h('td', {}, tAgo(q.asked)), h('td', {}, q.text),
            h('td', {}, h('code', {}, 'router.py reply ' + q.id + ' "…"')));
        }))));
    }
    var finished = d.rows.filter(function (r) { return r.state === 'done'; });
    if (finished.length) {
      out.push(h('h2', {}, 'Done'), h('div', {class: 'card'}, h('table', {},
        h('tr', {}, ['PR', 'What', 'Took', 'Steps run', 'Second attempts', 'Link'].map(function (x) { return h('th', {}, x); })),
        finished.map(function (r) {
          var again = r.steps.filter(function (s) { return s.n > 1; }).map(function (s) { return s.id + ' ×' + s.n; }).join(', ') || 'none';
          return h('tr', {}, h('td', {}, h('b', {}, r.id)), h('td', {}, r.title), h('td', {}, took(r.created, r.ended) || '?'),
            h('td', {}, r.steps.filter(function (s) { return s.status === 'done'; }).length + ' of ' + r.total), h('td', {}, again),
            h('td', {}, /^https?:/.test(r.url) ? h('a', {href: r.url, target: '_blank', rel: 'noopener'}, r.url) : '-'));
        }))));
    }
    var flow = d.flow;
    if (flow && flow.removed && flow.removed.length) {
      out.push(h('h2', {}, 'Taken out of the flow'), h('div', {class: 'card'}, h('table', {},
        h('tr', {}, h('th', {}, 'PR'), h('th', {}, 'What'), h('th', {}, 'Removed in')),
        flow.removed.map(function (x) { return h('tr', {}, h('td', {}, x.id), h('td', {}, x.title || '-'), h('td', {}, 'v' + x.v)); }))));
    }
    if (flow && flow.history && flow.history.length) {
      out.push(h('h2', {}, 'Flow versions'), h('div', {class: 'card'}, h('table', {},
        h('tr', {}, h('th', {}, 'Version'), h('th', {}, 'When'), h('th', {}, 'By'), h('th', {}, 'What changed')),
        flow.history.slice().reverse().map(function (x) {
          return h('tr', {}, h('td', {}, 'v' + x.v), h('td', {}, clock(x.at)), h('td', {}, x.by + (x.note ? ': ' + x.note : '')),
            h('td', {}, x.changes.length > 4 ? x.changes.slice(0, 4).join('; ') + '; and ' + (x.changes.length - 4) + ' more' : x.changes.join('; ')));
        }))));
    }
    if (d.events.length) {
      out.push(h('h2', {}, 'Last events'), h('div', {class: 'card'}, h('table', {},
        h('tr', {}, ['When', 'PR', 'Step', 'What'].map(function (x) { return h('th', {}, x); })),
        d.events.slice().reverse().map(function (e) { return h('tr', {}, h('td', {}, clock(e.t)), h('td', {}, e.pr), h('td', {}, e.step), h('td', {}, e.text)); }))));
    }
    out.push(h('div', {class: 'foot'}, 'Served by the mailbox daemon of ', h('code', {}, 'router.py'), ' on this machine only. It asks the router every ' +
      POLL_MS / 1000 + ' s. In a terminal: ', h('code', {}, 'router.py progress'), '. Every event: ', h('span', {class: 'mono'}, d.state_dir + '/journal.md')));
    return out;
  }

  // ------------------------------------------------------------ edit mode

  function setEdit(on) {
    if (on === S.edit) return;
    S.notice = null; S.form = null; S.review = null;
    if (on && !S.draft) {   // edit mode starts from the router's copy
      loadFlow().then(function () { S.edit = true; draw(); },
                      function (e) { S.notice = {cls: '', text: 'The flow could not be read: ' + e.message}; draw(); });
      return;
    }
    S.edit = on;
    draw();
  }

  function editTools(d) {
    var out = [];
    var f = S.draft, n = dirty();
    var base = S.copy ? S.copy.version : '?';
    out.push(h('div', {class: 'editbar'},
      h('span', {class: n ? 'dirty' : 'sub', id: 'edit-state'}, n ? 'Local edits on v' + base + ', not applied' : 'Editing v' + base + ': no local edit'),
      h('label', {}, 'slots', h('input', {type: 'number', min: '1', value: String(f.slots || ''), id: 'slots',
        onchange: function (e) { var v = parseInt(e.target.value, 10); f.slots = isNaN(v) ? e.target.value : v; edited(); draw(); }})),
      h('label', {}, 'start', h('select', {id: 'start', onchange: function (e) { f.start = e.target.value; edited(); draw(); }},
        ['auto', 'manual'].map(function (m) { return h('option', {value: m, selected: f.start === m}, m); }))),
      h('button', {type: 'button', class: 'btn', onclick: function () { S.form = {kind: 'pr', after: ''}; draw(); }}, '+ Add a PR'),
      h('span', {style: 'flex:1'}),
      h('button', {type: 'button', class: 'btn', id: 'discard', disabled: !n, onclick: discard}, 'Discard'),
      h('button', {type: 'button', class: 'btn primary', id: 'apply', disabled: !n || S.busy, onclick: function () { apply(true); }},
        S.busy ? 'Asking the router…' : 'Apply…')));
    out.push(h('div', {class: 'palette', id: 'palette'}, h('span', {class: 'lab'}, 'Drag a role into a PR:'),
      ((d.flow && d.flow.palette) || []).map(function (r, i) {
        var kind = r.role === 'gate' || r.role === 'script';
        return h('span', {class: 'role' + (kind ? ' kind' : ''), dataset: {drag: 'role', role: String(i), name: r.role},
                          role: 'button', 'aria-label': 'role ' + r.role,
                          title: kind ? 'a ' + r.role + ' step' : r.role + ': ' + who(r.step)}, r.role);
      })));
    if (S.review) out.push(reviewPanel());
    if (S.problems.length)
      out.push(h('div', {class: 'panel bad', id: 'problems'}, h('h3', {}, 'The router refused the edit: nothing changed, your edits are kept'),
        h('ul', {}, S.problems.map(function (x) { return h('li', {}, x); }))));
    if (S.form && S.form.kind === 'step') out.push(stepForm());
    if (S.form && S.form.kind === 'pr') out.push(prForm());
    return out;
  }

  function reviewPanel() {
    var r = S.review;
    return h('div', {class: 'panel', id: 'review'},
      h('h3', {}, 'Apply to v' + r.base + ': the router will make v' + (r.base + 1) + ' with ' + r.changes.length + ' change' + (r.changes.length === 1 ? '' : 's')),
      h('ul', {}, r.changes.map(function (x) { return h('li', {}, x); })),
      h('div', {class: 'form'}, h('label', {for: 'note'}, 'why (goes in the history)'), h('input', {id: 'note', value: r.note || '',
        oninput: function (e) { r.note = e.target.value; }}),
        h('div', {class: 'row'}, h('button', {type: 'button', class: 'btn primary', id: 'confirm', disabled: S.busy, onclick: function () { apply(false); }}, 'Apply as v' + (r.base + 1)),
          h('button', {type: 'button', class: 'btn', onclick: function () { S.review = null; draw(); }}, 'Back to the canvas'))));
  }

  function field(label, id, value, extra) {
    return [h('label', {for: id}, label), h('input', Object.assign({id: id, name: id, value: value || ''}, extra || {}))];
  }

  function stepForm() {
    var p = draftPr(S.form.pid), s = p && stepsOf(p)[stepIndex(stepsOf(p), S.form.sid)];
    if (!s) { S.form = null; return null; }
    var type = s.type || 'worker';
    var fields = type === 'worker' ? [['agent', 'agent'], ['model', 'model'], ['effort', 'effort']] : type === 'gate' ? [['title', 'title']] : [['run', 'run']];
    return h('form', {class: 'panel', id: 'step-form', onsubmit: function (e) {
        e.preventDefault();
        var t = ownSteps(p)[stepIndex(stepsOf(p), s.id)];
        fields.forEach(function (x) { var v = e.target.elements[x[0]].value.trim(); if (v) t[x[0]] = v; else delete t[x[0]]; });
        S.form = null; edited(); draw();
      }},
      h('h3', {}, p.id + ' · step ' + s.id + ' (' + type + (s.spec ? ', ' + s.spec : '') + ')'),
      h('div', {class: 'form'}, fields.map(function (x) { return field(x[1], x[0], s[x[0]]); }),
        h('div', {class: 'row'}, h('button', {type: 'submit', class: 'btn primary'}, 'Keep'),
          h('button', {type: 'button', class: 'btn', onclick: function () { removeStep(p.id, s.id); }}, 'Remove the step'),
          h('button', {type: 'button', class: 'btn', onclick: function () { S.form = null; draw(); }}, 'Cancel'))));
  }

  function prForm() {
    var anchorPr = S.form.after ? draftPr(S.form.after) : null;
    var templates = (S.data.flow && S.data.flow.templates) || [];
    var parts = [];
    (S.draft.prs || []).forEach(function (p) { if (p.part && parts.indexOf(p.part) < 0) parts.push(p.part); });
    var err = S.form.error;
    return h('form', {class: 'panel', id: 'pr-form', onsubmit: function (e) {
        e.preventDefault();
        var el = e.target.elements, vars = {}, bad = [];
        el.vars.value.split('\n').forEach(function (line) {
          if (!line.trim()) return;
          var i = line.indexOf('=');
          if (i < 1) bad.push(line); else vars[line.slice(0, i).trim()] = line.slice(i + 1);
        });
        var id = el.id.value.trim();
        if (!id) { S.form.error = 'a PR needs an id'; draw(); return; }
        if (bad.length) { S.form.error = 'vars: one NAME=value per line, not ' + JSON.stringify(bad[0]); draw(); return; }
        var p = {id: id, part: el.part.value.trim(), title: el.title.value.trim()};
        if (el.base.value.trim()) p.base = el.base.value.trim();
        p.after = el.after.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean);
        p.template = el.template.value;
        if (Object.keys(vars).length) p.vars = vars;
        var prs = S.draft.prs, at = anchorPr ? prs.indexOf(anchorPr) + 1 : prs.length;
        prs.splice(at, 0, p);
        S.form = null; edited(); draw();
      }},
      h('h3', {}, anchorPr ? 'A new PR after ' + anchorPr.id : 'A new PR'),
      err ? h('div', {class: 'late'}, err) : null,
      h('div', {class: 'form'},
        field('id', 'id', ''), field('part', 'part', anchorPr ? anchorPr.part : '', {list: 'parts'}),
        h('datalist', {id: 'parts'}, parts.map(function (x) { return h('option', {value: x}); })),
        field('title', 'title', ''), field('base', 'base', anchorPr ? anchorPr.base : ''),
        field('after (ids, comma-separated)', 'after', anchorPr ? anchorPr.id : ''),
        h('label', {for: 'template'}, 'template'),
        h('select', {id: 'template', name: 'template'}, templates.map(function (t) {
          return h('option', {value: t, selected: anchorPr ? anchorPr.template === t : false}, t);
        })),
        h('label', {for: 'vars'}, 'vars (NAME=value, one per line)'), h('textarea', {id: 'vars', name: 'vars', rows: '3'}),
        h('div', {class: 'row'}, h('button', {type: 'submit', class: 'btn primary'}, 'Add'),
          h('button', {type: 'button', class: 'btn', onclick: function () { S.form = null; draw(); }}, 'Cancel'))));
  }

  function removeStep(pid, sid) {
    var p = draftPr(pid), i = stepIndex(stepsOf(p), sid);
    if (i < fixedOf(pid)) { S.notice = {cls: '', text: 'step ' + sid + ' is settled: it cannot be removed'}; draw(); return; }
    ownSteps(p).splice(i, 1);
    S.form = null; edited(); draw();
  }

  function dropPr(pid) {
    if ((serverPr(pid) || {}).started) return;
    S.draft.prs = S.draft.prs.filter(function (p) { return lc(p.id) !== lc(pid); });
    edited(); draw();
  }

  function discard() {
    S.draft = userPart(clone(S.copy)); S.form = null; edited(); draw();
  }

  // Apply: a dry run first, whose lines the owner reads; then the apply. Both are POST /flow, i.e. `flow apply`.
  function apply(dry) {
    var base = S.copy.version, note = S.review ? S.review.note || '' : '';
    S.busy = true; draw();
    post({base: base, by: 'page', note: note, dry_run: dry, flow: S.draft}).then(function (r) {
      S.busy = false;
      if (r.status === 200 && dry) {
        S.problems = [];
        if (r.body.changes.length) S.review = {base: base, changes: r.body.changes, note: ''};
        else S.notice = {cls: '', text: 'The router sees no change in your edits: the flow stays v' + base + '.'};
        draw();
        return null;
      }
      S.review = null;
      if (r.status === 200) {
        S.problems = [];
        S.notice = {cls: 'okay', text: 'Applied: the flow is v' + r.body.version + ' (' + r.body.changes.length + ' change' +
          (r.body.changes.length === 1 ? '' : 's') + ')' + (r.body.started && r.body.started.length ? '. ' + r.body.started.join('; ') : '') + '.'};
        return refresh();
      }
      if (r.status === 409) {
        var v = r.body.current ? r.body.current.version : '?';
        S.problems = [];
        S.notice = {cls: 'wait', text: 'Someone else changed the flow (v' + v + '): reload and redo. The canvas now shows v' + v + '.'};
        return refresh();
      }
      S.problems = r.body.problems || [r.body.reason || 'the router answered ' + r.status];
      draw();
      return null;
    }).catch(function (e) {
      S.busy = false; S.review = null;
      S.problems = ['the router could not be reached: ' + e.message + '. Nothing was applied.'];
      draw();
    });
  }

  // After an apply, everything comes from the router again: the flow it now holds and the state of the run.
  function refresh() {
    return Promise.all([loadFlow(), getJSON('/state').then(function (d) { S.data = d; S.lastOk = Date.now(); })]).then(draw, function () { draw(); });
  }

  // ------------------------------------------------------------ dragging (pointer events: a mouse, a pen, a finger)

  function targetAt(x, y) {
    var e = document.elementFromPoint(x, y);
    if (!e) return null;
    var c = e.closest('[data-step]'), row = e.closest('[data-steps]');
    if (c && c.dataset.drag !== 'role') {
      var r = c.getBoundingClientRect(), after = x > r.left + r.width / 2;
      return {pid: c.dataset.pr, index: +c.dataset.idx + (after ? 1 : 0), el: c, side: after ? 'after' : 'before',
              locked: c.dataset.locked === '1', sid: c.dataset.step, status: c.dataset.status};
    }
    if (row) return {pid: row.dataset.steps, index: +row.dataset.n, el: row, side: 'end'};
    return null;
  }

  function unmark() {
    document.querySelectorAll('.drop-before,.drop-after,.drop-end,.no-drop').forEach(function (e) {
      e.classList.remove('drop-before', 'drop-after', 'drop-end', 'no-drop');
    });
  }

  document.addEventListener('pointerdown', function (e) {
    if (!S.edit || e.button !== 0 || S.busy) return;
    var src = e.target.closest('[data-drag]');
    if (!src || e.target.closest('button,input,select,textarea')) return;
    var pal = (S.data.flow && S.data.flow.palette) || [];
    S.drag = {x: e.clientX, y: e.clientY, on: false, src: src, kind: src.dataset.drag, pid: src.dataset.pr, sid: src.dataset.step,
              step: src.dataset.drag === 'role' ? pal[+src.dataset.role].step : null,
              label: src.dataset.drag === 'role' ? src.dataset.name : src.dataset.step};
    e.preventDefault();
  });

  document.addEventListener('pointermove', function (e) {
    var d = S.drag;
    if (!d) return;
    if (!d.on) {
      if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) < 5) return;
      d.on = true;
      d.ghost = h('div', {class: 'ghost'}, d.label);
      document.body.appendChild(d.ghost);
      document.body.classList.add('dragging');
      d.src.classList.add('src');
    }
    d.ghost.style.left = (e.clientX + 10) + 'px';
    d.ghost.style.top = (e.clientY + 10) + 'px';
    unmark();
    var t = targetAt(e.clientX, e.clientY);
    if (t) t.el.classList.add(dropRefusal(d, t) ? 'no-drop' : 'drop-' + t.side);
  });

  function endDrag(e, cancel) {
    var d = S.drag;
    if (!d) return;
    S.drag = null;
    unmark();
    if (d.ghost) d.ghost.remove();
    document.body.classList.remove('dragging');
    d.src.classList.remove('src');
    if (cancel) { if (S.held) draw(); return; }
    if (!d.on) {   // a click: a step opens its form
      if (d.kind === 'step') { S.form = {kind: 'step', pid: d.pid, sid: d.sid}; draw(); }
      else if (S.held) draw();
      return;
    }
    var t = targetAt(e.clientX, e.clientY), why = dropRefusal(d, t);
    if (!t) { if (S.held) draw(); return; }
    if (why) S.notice = {cls: 'bad', text: 'Not dropped: ' + why + '.'};
    else if (drop(d, t)) edited();
    draw();
  }
  document.addEventListener('pointerup', function (e) { endDrag(e, false); });
  document.addEventListener('pointercancel', function (e) { endDrag(e, true); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && S.drag) endDrag(e, true); });

  // ------------------------------------------------------------ the clock

  function tick() {
    var now = nowS();
    document.querySelectorAll('[data-t]').forEach(function (e) {
      var t = epoch(e.dataset.t); if (!t) return;
      var s = now - t;
      e.textContent = e.dataset.mode === 'for' ? dur(s) : s < 60 ? 'just now' : dur(s) + ' ago';
      if (e.dataset.warn) e.classList.toggle('late', s > parseFloat(e.dataset.warn));
    });
    var since = (Date.now() - (S.lastOk || S.since)) / 1000;
    var ref = document.getElementById('refreshed');
    if (ref) ref.textContent = S.lastOk ? (since < 2 ? 'just now' : Math.round(since) + ' s ago') : 'never';
    var st = document.getElementById('stale');
    if (st) {
      st.hidden = since < STALE_S;
      st.querySelector('b').textContent = Math.round(since) + ' s';
    }
    var app = document.getElementById('app');
    if (!S.data && since >= STALE_S && app)
      app.textContent = 'The router has not answered for ' + Math.round(since) + ' s. The mailbox daemon serves this page: router.py status.';
  }

  window.addEventListener('resize', edges);
  poll();
  setInterval(poll, POLL_MS);
  setInterval(tick, 1000);
})();
