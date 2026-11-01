#!/usr/bin/env python3
"""blog-pdf web service: paste an article URL, get a print-ready PDF in Google Drive.

Binds to 127.0.0.1 only; the public URL is a Cloudflare Tunnel with Cloudflare
Access in front. Requests that arrive through Cloudflare (Cf-Ray header) must
carry an Access JWT, so a misconfigured Access app fails closed.
"""
import html, json, os, queue, re, threading, time, traceback, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pipeline

JOBS_FILE = pipeline.DATA / 'jobs.json'
pipeline.DATA.mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get('PORT', '8099'))
lock = threading.Lock()
jobs = json.loads(JOBS_FILE.read_text()) if JOBS_FILE.exists() else {}
work = queue.Queue()


def save():
    with lock:
        tmp = JOBS_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(jobs, indent=1))
        tmp.replace(JOBS_FILE)


def update(jid, **kw):
    with lock:
        jobs[jid].update(kw)
    save()


def worker():
    while True:
        jid = work.get()
        job = jobs[jid]
        log = []

        def say(msg):
            log.append(str(msg))
            update(jid, log=log[-20:])
        def prog(frac, stage):
            update(jid, progress=round(frac, 3), stage=stage)
        update(jid, status='running', started=time.time(), progress=0, stage='Starting')
        try:
            res = pipeline.convert(job['url'], log=say, progress=prog)
            update(jid, status='done', result=res, finished=time.time(), progress=1, stage='Done')
        except Exception as e:
            say(traceback.format_exc(limit=3))
            update(jid, status='failed', error=str(e), finished=time.time())


# resume anything interrupted by a restart
for jid, j in sorted(jobs.items(), key=lambda kv: kv[1]['created']):
    if j['status'] in ('queued', 'running'):
        j['status'] = 'queued'
        work.put(jid)

PAGE = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Blog → PDF</title>
<style>
:root { --bg:#fafaf8; --fg:#1d1d1b; --mute:#6b6a65; --card:#fff; --line:#e4e2dc; --acc:#c2410c; --ok:#15803d; --bad:#b91c1c; }
@media (prefers-color-scheme: dark) { :root { --bg:#161615; --fg:#ecebe6; --mute:#a3a29c; --card:#1f1f1d; --line:#33322f; --acc:#fb923c; --ok:#4ade80; --bad:#f87171; } }
* { box-sizing: border-box; } body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; }
main { max-width: 720px; margin: 0 auto; padding: 28px 16px 60px; }
h1 { font-size: 22px; margin: 0 0 4px; } .sub { color: var(--mute); margin: 0 0 20px; font-size: 14px; }
form { display:flex; gap:8px; } input { flex:1; min-width:0; font:inherit; padding:11px 12px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--fg); }
button { font:inherit; font-weight:600; padding:11px 16px; border:0; border-radius:8px; background:var(--acc); color:#fff; cursor:pointer; }
.job { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; margin-top:12px; }
.job .t { font-weight:600; overflow-wrap:anywhere; } .job .u { color:var(--mute); font-size:13px; overflow-wrap:anywhere; }
.job .row { display:flex; gap:14px; flex-wrap:wrap; margin-top:6px; font-size:14px; } a { color: var(--acc); }
.st { font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; }
.done { color:var(--ok); } .failed { color:var(--bad); } .running, .queued { color:var(--mute); }
.job .head { display:flex; justify-content:space-between; align-items:center; gap:8px; }
.redo { padding:4px 10px; font-size:13px; font-weight:600; background:transparent; color:var(--acc); border:1px solid var(--line); }
.redo:disabled { color:var(--mute); cursor:default; }
.bar { height:6px; background:var(--line); border-radius:3px; overflow:hidden; margin-top:8px; }
.bar i { display:block; height:100%; background:var(--acc); border-radius:3px; transition:width .6s ease; }
.bar.wait i { width:30% !important; animation: slide 1.2s ease-in-out infinite; opacity:.5; }
@keyframes slide { from { transform:translateX(-100%); } to { transform:translateX(340%); } }
.prog { display:flex; justify-content:space-between; gap:10px; font-size:13px; color:var(--mute); margin-top:5px; font-variant-numeric: tabular-nums; }
.took { color:var(--mute); }
.note { font-size:13px; color:var(--mute); margin-top:4px; } .err { font-size:13px; color:var(--bad); margin-top:4px; overflow-wrap:anywhere; }
.meta { font-size:13px; color:var(--mute); display:flex; gap:6px; align-items:baseline; flex-wrap:wrap; min-width:0; }
.tabs { display:flex; gap:4px; margin:26px 0 4px; border-bottom:1px solid var(--line); }
.tabs button { background:transparent; color:var(--mute); border-radius:0; padding:8px 12px; border-bottom:2px solid transparent; margin-bottom:-1px; }
.tabs button.on { color:var(--fg); border-bottom-color:var(--acc); }
.tabs .count { margin-left:auto; align-self:center; font-size:13px; color:var(--mute); }
h2.day { font-size:13px; font-weight:700; color:var(--mute); text-transform:uppercase; letter-spacing:.05em; margin:22px 0 0; }
h2.day span { font-weight:500; text-transform:none; letter-spacing:0; }
.empty { color:var(--mute); margin-top:20px; font-size:14px; }
.cal { margin-top:16px; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; }
.calhead { display:flex; align-items:center; gap:8px; margin-bottom:10px; }
.calhead b { flex:1; } .calhead span { font-size:13px; color:var(--mute); }
.nav { background:transparent; color:var(--fg); border:1px solid var(--line); padding:4px 11px; }
.grid { display:grid; grid-template-columns:repeat(7, 1fr); gap:4px; }
.grid .dow { font-size:11px; color:var(--mute); text-align:center; padding-bottom:2px; }
.cell { aspect-ratio:1; border-radius:7px; padding:4px; font-size:12px; font-weight:500; display:flex; flex-direction:column; justify-content:space-between;
  align-items:flex-start; background:var(--bg); color:var(--mute); border:1px solid transparent; }
.cell i { font-style:normal; font-weight:700; font-size:13px; align-self:flex-end; }
.cell.l1 { background:color-mix(in srgb, var(--acc) 18%, var(--card)); color:var(--fg); }
.cell.l2 { background:color-mix(in srgb, var(--acc) 38%, var(--card)); color:var(--fg); }
.cell.l3 { background:color-mix(in srgb, var(--acc) 62%, var(--card)); color:#fff; }
.cell.l4 { background:var(--acc); color:#fff; }
.cell.today { border-color:var(--fg); } .cell.sel { outline:2px solid var(--acc); outline-offset:1px; }
.cell.future, .nav:disabled { opacity:.4; cursor:default; }
</style></head><body><main>
<h1>Blog → PDF</h1><p class="sub">Print-ready A4 with a summary page. PDFs land in Drive › Blog PDFs › today (when rclone is set up).</p>
<form id="f"><input name="url" type="url" placeholder="https://…" required autofocus><button>Convert</button></form>
<div id="active"></div>
<div class="tabs"><button data-view="recent">Recent</button><button data-view="calendar">Calendar</button><span class="count" id="count"></span></div>
<div id="view"></div></main>
<script>
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt = s => { s = Math.max(0, Math.round(s)); return s < 60 ? s + 's' : Math.floor(s / 60) + 'm ' + String(s % 60).padStart(2, '0') + 's'; };
let state = { list: [], skew: 0, typical: 30, at: 0 };
function eta(j) {
  // blend the historical typical duration (early, when progress says little) with the live rate
  const el = Date.now() / 1000 - state.skew - j.started, p = j.progress || 0;
  const fromRate = p > 0.05 ? el / p * (1 - p) : Infinity;
  const fromHist = Math.max(state.typical - el, 3);
  const w = Math.min(1, p / 0.5);
  const left = isFinite(fromRate) ? w * fromRate + (1 - w) * fromHist : fromHist;
  return { el, left };
}
function progHtml(j, queuePos) {
  if (j.status === 'queued') {
    const wait = queuePos * state.typical;
    return `<div class="bar wait"><i></i></div><div class="prog"><span>Waiting in queue${queuePos ? ` (${queuePos} ahead)` : ''}</span><span>ETA ~${fmt(wait + state.typical)}</span></div>`;
  }
  const { el, left } = eta(j);
  const pct = Math.round((j.progress || 0) * 100);
  return `<div class="bar"><i style="width:${Math.max(3, pct)}%"></i></div>
    <div class="prog"><span>${esc(j.stage || 'Working')} · ${pct}%</span><span>${fmt(el)} elapsed · ~${fmt(left)} left</span></div>`;
}
const pad = n => String(n).padStart(2, '0');
const dayKey = t => { const d = new Date(t * 1000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const keyDate = k => { const [y, m, d] = k.split('-').map(Number); return new Date(y, m - 1, d); };
const site = u => { try { return new URL(u).hostname.replace(/^www\\./, ''); } catch { return ''; } };
const clock = t => new Date(t * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
function dayLabel(k) {
  const now = Date.now() / 1000;
  if (k === dayKey(now)) return 'Today';
  if (k === dayKey(now - 86400)) return 'Yesterday';
  const d = keyDate(k);
  return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short',
    year: d.getFullYear() !== new Date().getFullYear() ? 'numeric' : undefined });
}
let view = 'recent', month = null, selDay = dayKey(Date.now() / 1000);
try { view = localStorage.getItem('view') || 'recent'; } catch {}
if (location.hash === '#calendar' || location.hash === '#recent') view = location.hash.slice(1);

function card(j, opts = {}) {
  const res = j.result || {};
  const fin = j.status === 'done' || j.status === 'failed';
  const took = fin && j.started && j.finished
    ? `<span class="took">${j.status === 'done' ? 'took' : 'failed after'} ${fmt(j.finished - j.started)}</span>` : '';
  const links = j.status === 'done' ? `<div class="row">
      ${res.drive ? `<a href="${esc(res.drive)}" target="_blank">Open in Drive</a>` : ''}
      <a href="files/${esc(res.pdf)}">Download PDF</a><a href="files/${esc(res.html)}" target="_blank">Preview</a>
      <span>${res.pages} pages · ${res.words} words</span>${took}</div>` : '';
  const notes = (res.notes || []).map(n => `<div class="note">⚠ ${esc(n)}</div>`).join('');
  const err = j.error ? `<div class="err">${esc(j.error)} ${took}</div>` : '';
  const prog = fin ? '' : progHtml(j, opts.queuePos || 0);
  const redo = fin ? `<button class="redo" data-url="${esc(j.url)}">Redo</button>` : '';
  const meta = [esc(site(j.url)), clock(j.finished || j.created)];
  if (opts.runs > 1) meta.push(`${opts.runs} runs`);
  return `<div class="job"><div class="head"><span class="meta"><span class="st ${j.status}">${j.status}</span><span>${meta.join(' · ')}</span></span>${redo}</div>
    <div class="t">${esc(res.title || '')}</div>${res.title ? '' : `<div class="u">${esc(j.url)}</div>`}${links}${notes}${err}${prog}</div>`;
}

// finished jobs, one entry per URL (newest attempt wins, redos collapse into it)
function finished() {
  const byUrl = new Map();
  for (const j of state.list.filter(j => j.status === 'done' || j.status === 'failed').sort((a, b) => b.created - a.created)) {
    const e = byUrl.get(j.url);
    if (e) e.runs++; else byUrl.set(j.url, { j, runs: 1 });
  }
  return [...byUrl.values()];
}
const grouped = entries => {
  const days = new Map();
  for (const e of entries) {
    const k = dayKey(e.j.finished || e.j.created);
    (days.get(k) || days.set(k, []).get(k)).push(e);
  }
  return days;
};
const dayHtml = (k, entries) => {
  const n = entries.filter(e => e.j.status === 'done').length;
  return `<h2 class="day">${dayLabel(k)} <span>· ${n} PDF${n === 1 ? '' : 's'}</span></h2>`
    + entries.map(e => card(e.j, { runs: e.runs })).join('');
};

function calendarHtml(entries) {
  const counts = {};
  for (const e of entries) if (e.j.status === 'done') { const k = dayKey(e.j.finished); counts[k] = (counts[k] || 0) + 1; }
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const m = month || new Date(today.getFullYear(), today.getMonth(), 1);
  const first = (m.getDay() + 6) % 7, days = new Date(m.getFullYear(), m.getMonth() + 1, 0).getDate();
  let total = 0, cells = '';
  for (let i = 0; i < first; i++) cells += '<span></span>';
  for (let d = 1; d <= days; d++) {
    const date = new Date(m.getFullYear(), m.getMonth(), d), k = dayKey(date / 1000), n = counts[k] || 0;
    total += n;
    const lvl = n >= 5 ? 4 : n >= 3 ? 3 : n;
    const cls = ['cell', n ? 'l' + lvl : '', +date === +today ? 'today' : '', k === selDay ? 'sel' : '', date > today ? 'future' : ''].join(' ');
    cells += `<button class="${cls}" data-day="${k}" ${date > today ? 'disabled' : ''}><span>${d}</span>${n ? `<i>${n}</i>` : ''}</button>`;
  }
  const isNow = m.getFullYear() === today.getFullYear() && m.getMonth() === today.getMonth();
  return `<div class="cal"><div class="calhead"><button class="nav" data-m="-1" aria-label="Previous month">‹</button>
    <b>${m.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}</b><span>${total} PDF${total === 1 ? '' : 's'}</span>
    <button class="nav" data-m="1" aria-label="Next month" ${isNow ? 'disabled' : ''}>›</button></div>
    <div class="grid">${['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].map(d => `<span class="dow">${d}</span>`).join('')}${cells}</div></div>`;
}

function render() {
  const active = state.list.filter(j => j.status === 'running' || j.status === 'queued').sort((a, b) => a.created - b.created);
  document.getElementById('active').innerHTML = active.map((j, i) => card(j, { queuePos: i })).join('');
  const entries = finished(), days = grouped(entries);
  const done = entries.filter(e => e.j.status === 'done').length;
  document.getElementById('count').textContent = `${done} PDF${done === 1 ? '' : 's'}`;
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.view === view));
  let out;
  if (view === 'calendar') {
    const sel = selDay && days.get(selDay);
    out = calendarHtml(entries) + (selDay
      ? (sel ? dayHtml(selDay, sel) : `<p class="empty">Nothing on ${dayLabel(selDay)}.</p>`)
      : '<p class="empty">Pick a day to see what you converted.</p>');
  } else {
    out = [...days].map(([k, e]) => dayHtml(k, e)).join('') || '<p class="empty">Nothing converted yet.</p>';
  }
  document.getElementById('view').innerHTML = out;
}
let timer;
async function load() {
  clearTimeout(timer);
  const d = await (await fetch('jobs')).json();
  state = { list: d.jobs, skew: Date.now() / 1000 - d.now, typical: d.typical };
  render();
  if (d.jobs.some(j => j.status === 'queued' || j.status === 'running')) timer = setTimeout(load, 2000);
}
// only the active cards tick; re-rendering the whole view each second would fight clicks
setInterval(() => {
  const active = state.list.filter(j => j.status === 'running' || j.status === 'queued').sort((a, b) => a.created - b.created);
  if (active.some(j => j.status === 'running'))
    document.getElementById('active').innerHTML = active.map((j, i) => card(j, { queuePos: i })).join('');
}, 1000);
document.body.onclick = async e => {
  const t = e.target.closest('button'); if (!t) return;
  if (t.classList.contains('redo')) {
    t.disabled = true; t.textContent = 'Queued';
    await fetch('jobs', { method: 'POST', body: new URLSearchParams({ url: t.dataset.url }) }); return load();
  }
  if (t.dataset.view) { view = t.dataset.view; try { localStorage.setItem('view', view); } catch {} history.replaceState(null, '', '#' + view); return render(); }
  if (t.dataset.m) {
    const m = month || new Date(new Date().getFullYear(), new Date().getMonth(), 1);
    month = new Date(m.getFullYear(), m.getMonth() + +t.dataset.m, 1); return render();
  }
  if (t.dataset.day) { selDay = selDay === t.dataset.day ? null : t.dataset.day; return render(); }
};
document.getElementById('f').onsubmit = async e => {
  e.preventDefault(); const fd = new FormData(e.target);
  await fetch('jobs', { method: 'POST', body: new URLSearchParams(fd) }); e.target.reset(); load();
};
const shared = new URLSearchParams(location.search).get('url');   // ?url=… from a bookmarklet/shortcut
if (shared) { fetch('jobs', { method:'POST', body:new URLSearchParams({url:shared}) }).then(() => { history.replaceState(null,'',location.pathname); load(); }); }
load();
</script></body></html>'''


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass

    def guard(self):
        if self.headers.get('Cf-Ray') and not self.headers.get('Cf-Access-Jwt-Assertion'):
            self.send(403, 'text/plain', b'forbidden: Cloudflare Access required')
            return False
        return True

    def send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.guard():
            return
        path = urlparse(self.path).path
        if path == '/':
            return self.send(200, 'text/html; charset=utf-8', PAGE.encode())
        if path == '/jobs':
            with lock:
                # everything (the calendar needs history), minus the bulky per-job logs
                lst = [{k: v for k, v in j.items() if k != 'log'}
                       for j in sorted(jobs.values(), key=lambda j: -j['created'])[:2000]]
                took = sorted(j['finished'] - j['started'] for j in jobs.values()
                              if j['status'] == 'done' and j.get('started') and j.get('finished'))[-20:]
            typical = took[len(took) // 2] if took else 30
            return self.send(200, 'application/json',
                             json.dumps({'now': time.time(), 'typical': typical, 'jobs': lst}).encode())
        m = re.fullmatch(r'/jobs/([0-9a-f]{12})', path)
        if m and m.group(1) in jobs:
            return self.send(200, 'application/json', json.dumps(jobs[m.group(1)]).encode())
        m = re.fullmatch(r'/files/([A-Za-z0-9._-]+\.(pdf|html))', path)
        if m:
            f = pipeline.OUT / m.group(1)
            if f.is_file():
                ctype = 'application/pdf' if m.group(2) == 'pdf' else 'text/html; charset=utf-8'
                extra = {'Content-Disposition': f'inline; filename="{f.name}"'}
                return self.send(200, ctype, f.read_bytes(), extra)
        if path == '/healthz':
            return self.send(200, 'text/plain', b'ok')
        self.send(404, 'text/plain', b'not found')

    def do_POST(self):
        if not self.guard():
            return
        if urlparse(self.path).path != '/jobs':
            return self.send(404, 'text/plain', b'not found')
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(min(n, 10000)).decode()
        ctype = self.headers.get('Content-Type', '')
        url = (json.loads(raw).get('url') if 'json' in ctype else (parse_qs(raw).get('url') or [''])[0]).strip()
        if not re.match(r'https?://[^\s/]+\.[^\s]+$', url or ''):
            return self.send(400, 'application/json', b'{"error":"need an http(s) url"}')
        jid = uuid.uuid4().hex[:12]
        with lock:
            jobs[jid] = {'id': jid, 'url': url, 'status': 'queued', 'created': time.time()}
        save()
        work.put(jid)
        self.send(202, 'application/json', json.dumps({'id': jid}).encode())


if __name__ == '__main__':
    threading.Thread(target=worker, daemon=True).start()
    print(f'blog-pdf on http://127.0.0.1:{PORT}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
