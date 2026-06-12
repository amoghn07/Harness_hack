"""Read-only health dashboard — a stdlib HTTP server over the agent's memory.

Zero dependencies (uses only http.server + the existing Storage layer), so it
runs anywhere the rest of the project does and over either backend (SQLite or
ClickHouse). It surfaces what the agent has stored: per-repo health score, the
signal breakdown, the score trend, and the specific offenders (outdated deps,
stale issues).

    python -m repohealth.dashboard --port 8000
    python -m repohealth dashboard --port 8000     # same, via the main CLI

The score/breakdown is computed on the fly from the stored snapshot (same code
path as `repohealth score`); the trend comes from the append-only `scores`
table that every `run` cycle records. Ingest/run a repo first, then open it.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .config import Config
from .detect import _labels
from .ingest import build_storage
from .scoring import score_repo
from .storage import Storage


# --------------------------------------------------------------------------- #
# Data access — small functions returning JSON-ready dicts.
# --------------------------------------------------------------------------- #
def list_repos(storage: Storage) -> list[str]:
    repos: set[str] = set()
    for table in ("scores", "issues", "deps", "ci_runs"):
        try:
            for (r,) in storage.query(f"SELECT DISTINCT repo FROM {table}"):
                if r:
                    repos.add(r)
        except Exception:
            pass  # table may not exist yet on a fresh store
    return sorted(repos)


def _history(storage: Storage, repo: str) -> list[dict]:
    rows = storage.query(
        "SELECT score, timestamp FROM scores WHERE repo=? ORDER BY timestamp", [repo]
    )
    return [{"score": int(s), "timestamp": str(t)} for s, t in rows]


def repo_summary(storage: Storage, repo: str) -> dict:
    hist = _history(storage, repo)
    outdated = storage.query(
        "SELECT COUNT(*) FROM deps WHERE repo=? AND outdated=1", [repo])[0][0]
    stale = storage.query(
        "SELECT COUNT(*) FROM issues WHERE repo=? AND state='open' "
        "AND age_days > 90", [repo])[0][0]
    return {
        "repo": repo,
        "latest_score": hist[-1]["score"] if hist else None,
        "samples": len(hist),
        "outdated_deps": int(outdated or 0),
        "stale_issues": int(stale or 0),
        "spark": [h["score"] for h in hist][-20:],
    }


def repo_detail(storage: Storage, repo: str) -> dict:
    score = score_repo(storage, repo)
    signals = [
        {"key": s.key, "label": s.label, "weight": s.weight,
         "detail": s.detail, "badness": s.badness, "penalty": round(s.penalty, 1)}
        for s in score.signals
    ]
    deps = [
        {"name": n, "current": c, "latest": l, "ecosystem": e, "source": sf}
        for n, c, l, e, sf in storage.query(
            "SELECT name, current_ver, latest_ver, ecosystem, source_file "
            "FROM deps WHERE repo=? AND outdated=1 ORDER BY ecosystem, name", [repo])
    ]
    issues = [
        {"id": int(i), "title": t or "", "age_days": int(a), "labels": _labels(lb)}
        for i, t, a, lb in storage.query(
            "SELECT id, title, age_days, labels FROM issues WHERE repo=? "
            "AND state='open' AND age_days > 90 ORDER BY age_days DESC", [repo])
    ]
    return {
        "repo": repo,
        "score": score.score,
        "computed_at": str(score.computed_at),
        "signals": signals,
        "outdated_deps": deps,
        "stale_issues": issues,
        "history": _history(storage, repo),
    }


# --------------------------------------------------------------------------- #
# HTTP server.
# --------------------------------------------------------------------------- #
class _Handler(BaseHTTPRequestHandler):
    cfg: Config = Config()  # replaced in serve()

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(json.dumps(obj).encode("utf-8"), "application/json", status)

    def log_message(self, *args) -> None:  # quiet by default
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        # Each request gets its own Storage — sqlite connections aren't shareable
        # across the server's worker threads.
        storage = build_storage(self.cfg)
        try:
            if path == "/" or path == "/index.html":
                self._send(_INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/repos":
                self._json({"repos": [repo_summary(storage, r)
                                      for r in list_repos(storage)]})
            elif path == "/api/repo":
                repo = parse_qs(parsed.query).get("repo", [""])[0]
                if not repo:
                    self._json({"error": "missing ?repo="}, 400)
                else:
                    self._json(repo_detail(storage, repo))
            else:
                self._send(b"not found", "text/plain", 404)
        except Exception as exc:  # surface errors as JSON rather than a 500 page
            self._json({"error": str(exc)}, 500)
        finally:
            storage.close()


def serve(cfg: Config | None = None, port: int = 8000,
          host: str = "127.0.0.1") -> None:
    _Handler.cfg = cfg or Config.from_env()
    httpd = ThreadingHTTPServer((host, port), _Handler)
    print(f"repohealth dashboard → http://{host}:{port}  "
          f"(store={_Handler.cfg.store_backend}; Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="repohealth.dashboard")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind address; use 0.0.0.0 to expose when hosting")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)
    serve(port=args.port, host=args.host)
    return 0


# --------------------------------------------------------------------------- #
# Single-page UI. Vanilla JS + Chart.js (CDN) — no build step.
# --------------------------------------------------------------------------- #
_INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Repo Health</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --line:#30363d; --txt:#e6edf3;
          --muted:#8b949e; --green:#3fb950; --amber:#d29922; --red:#f85149; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
         background:var(--bg); color:var(--txt); display:flex; min-height:100vh; }
  aside { width:280px; border-right:1px solid var(--line); padding:16px;
          flex-shrink:0; }
  aside h1 { font-size:15px; margin:0 0 4px; }
  aside .sub { color:var(--muted); font-size:12px; margin-bottom:16px; }
  .repo { padding:10px 12px; border:1px solid var(--line); border-radius:8px;
          margin-bottom:8px; cursor:pointer; display:flex; justify-content:space-between;
          align-items:center; gap:8px; }
  .repo:hover { border-color:var(--muted); }
  .repo.active { border-color:#1f6feb; background:#161f2e; }
  .repo .name { font-weight:600; word-break:break-all; }
  .repo .meta { color:var(--muted); font-size:11px; }
  .badge { font-weight:700; padding:2px 8px; border-radius:999px; font-size:12px; }
  main { flex:1; padding:24px 32px; overflow:auto; }
  .head { display:flex; align-items:baseline; gap:16px; margin-bottom:20px; }
  .score-big { font-size:56px; font-weight:800; line-height:1; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:12px;
          padding:18px; }
  .card h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em;
             color:var(--muted); margin:0 0 14px; }
  .full { grid-column:1 / -1; }
  .sig { margin-bottom:12px; }
  .sig .row { display:flex; justify-content:space-between; font-size:12px; margin-bottom:4px; }
  .bar { height:8px; background:#21262d; border-radius:6px; overflow:hidden; }
  .bar > i { display:block; height:100%; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); }
  th { color:var(--muted); font-weight:600; font-size:11px; text-transform:uppercase; }
  code { background:#21262d; padding:1px 5px; border-radius:5px; }
  .tag { display:inline-block; background:#21262d; color:var(--muted);
         border-radius:999px; padding:1px 7px; font-size:11px; margin-right:4px; }
  .empty { color:var(--muted); padding:40px; text-align:center; }
  .arrow { color:var(--red); } .arrow b { color:var(--green); }
</style>
</head>
<body>
<aside>
  <h1>🩺 Repo Health</h1>
  <div class="sub">autonomous monitor & auto-fixer</div>
  <div id="repos"></div>
</aside>
<main id="main"><div class="empty">Loading…</div></main>
<script>
const color = s => s>=80 ? 'var(--green)' : s>=60 ? 'var(--amber)' : 'var(--red)';
let chart;

async function loadRepos() {
  const data = await (await fetch('/api/repos')).json();
  const el = document.getElementById('repos');
  if (!data.repos.length) { el.innerHTML = '<div class="empty">No repos stored yet.<br>Run an ingest first.</div>'; return; }
  el.innerHTML = '';
  data.repos.forEach((r,i) => {
    const d = document.createElement('div');
    d.className = 'repo'; d.dataset.repo = r.repo;
    const s = r.latest_score;
    d.innerHTML = `<div><div class="name">${r.repo}</div>
      <div class="meta">${r.outdated_deps} outdated · ${r.stale_issues} stale</div></div>
      <span class="badge" style="background:${s==null?'#21262d':color(s)};color:#0d1117">${s==null?'–':s}</span>`;
    d.onclick = () => selectRepo(r.repo, d);
    el.appendChild(d);
    if (i===0) d.click();
  });
}

async function selectRepo(repo, node) {
  document.querySelectorAll('.repo').forEach(n => n.classList.remove('active'));
  if (node) node.classList.add('active');
  const d = await (await fetch('/api/repo?repo='+encodeURIComponent(repo))).json();
  if (d.error) { document.getElementById('main').innerHTML = `<div class="empty">${d.error}</div>`; return; }
  render(d);
}

function render(d) {
  const deps = d.outdated_deps.map(x =>
    `<tr><td><span class="tag">${x.ecosystem}</span><code>${x.name}</code></td>
     <td class="arrow">${x.current} → <b>${x.latest}</b></td><td class="meta">${x.source}</td></tr>`).join('')
    || '<tr><td colspan="3" class="meta">none 🎉</td></tr>';
  const issues = d.stale_issues.map(x =>
    `<tr><td>#${x.id}</td><td>${x.title}</td><td>${x.age_days}d</td>
     <td>${x.labels.map(l=>`<span class="tag">${l}</span>`).join('')}</td></tr>`).join('')
    || '<tr><td colspan="4" class="meta">none 🎉</td></tr>';
  const sigs = d.signals.map(s => {
    const pct = Math.round(s.badness*100);
    return `<div class="sig"><div class="row"><span>${s.label}</span>
      <span class="meta">${s.detail} · −${s.penalty}</span></div>
      <div class="bar"><i style="width:${pct}%;background:${pct>50?'var(--red)':pct>20?'var(--amber)':'var(--green)'}"></i></div></div>`;
  }).join('');

  document.getElementById('main').innerHTML = `
    <div class="head">
      <div class="score-big" style="color:${color(d.score)}">${d.score}<span style="font-size:20px;color:var(--muted)">/100</span></div>
      <div><div style="font-weight:600;font-size:18px">${d.repo}</div>
      <div class="meta">computed ${d.computed_at.slice(0,19).replace('T',' ')}</div></div>
    </div>
    <div class="grid">
      <div class="card"><h2>Signal breakdown</h2>${sigs}</div>
      <div class="card"><h2>Health trend</h2><canvas id="trend" height="150"></canvas>
        <div id="notrend" class="meta" style="display:none">Only one sample so far — run more cycles.</div></div>
      <div class="card full"><h2>Outdated dependencies (${d.outdated_deps.length})</h2>
        <table><tr><th>Package</th><th>Update</th><th>Manifest</th></tr>${deps}</table></div>
      <div class="card full"><h2>Stale issues &gt;90d (${d.stale_issues.length})</h2>
        <table><tr><th>#</th><th>Title</th><th>Age</th><th>Labels</th></tr>${issues}</table></div>
    </div>`;

  const pts = d.history;
  if (chart) chart.destroy();
  if (pts.length < 2) { document.getElementById('notrend').style.display='block'; return; }
  chart = new Chart(document.getElementById('trend'), {
    type:'line',
    data:{ labels: pts.map(p=>p.timestamp.slice(5,10)),
      datasets:[{ data: pts.map(p=>p.score), borderColor:'#1f6feb',
        backgroundColor:'rgba(31,111,235,.15)', fill:true, tension:.3, pointRadius:2 }]},
    options:{ plugins:{legend:{display:false}},
      scales:{ y:{min:0,max:100,grid:{color:'#21262d'},ticks:{color:'#8b949e'}},
               x:{grid:{display:false},ticks:{color:'#8b949e'}}}}
  });
}
loadRepos();
</script>
</body>
</html>"""


if __name__ == "__main__":
    raise SystemExit(main())
