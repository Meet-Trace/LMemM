#!/usr/bin/env python3
"""A labelling page for your exported days, served only to this Mac (127.0.0.1).

    python3 scripts/export_real_days.py --from 2026-11-02 --to 2026-11-08
    python3 scripts/label_days.py            # then open the address it prints

For each thing you were on, type the project path it belongs to ("LMemM/Resume"), several separated
by ";" if it served more than one, or press "Nothing" for an inbox or a stray page. Every change is
saved straight into data/labelled-days/<day>.json. The page never suggests a grouping from any
algorithm, so your labels are not steered by what is being tested; it only offers names you typed.
"""
import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
from project_reasoner import _texts  # noqa: E402

DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def card(item):
    t = _texts(item)
    lines = [f"{k}: {line}" for k in ("typed", "received", "notes", "read", "screen") for line in t[k][-3:]]
    return {"id": item["id"], "app": item.get("app"), "kind": item.get("kind"), "title": item.get("title"),
            "details": {k: v for k, v in (item.get("state") or {}).items() if isinstance(v, (str, list))},
            "lines": lines[:8], "label": item.get("label") or [], "status": item.get("label_status") or "todo"}


def day_view(doc):
    """Things in the order first seen, with the visit times, for the page."""
    order, times = [], {}
    for v in doc.get("timeline") or []:
        if v["item"] not in times:
            order.append(v["item"])
        times.setdefault(v["item"], []).append(f"{v['from'][11:16]}-{v['to'][11:16]}")
    return [{**card(doc["items"][i]), "times": times[i]} for i in order if i in doc["items"]]


def apply_labels(doc, labels):
    """labels: {id: [paths] or None (still todo)}. Returns how many things are labelled."""
    for iid, paths in labels.items():
        item = doc["items"].get(iid)
        if item is None:
            continue
        if paths is None:
            item["label"], item["label_status"] = [], "todo"
        else:
            clean = [re.sub(r"\s*/\s*", "/", " ".join(str(p).split())).strip("/") for p in paths]
            item["label"], item["label_status"] = [p for p in dict.fromkeys(clean) if p], "done"
    return sum(1 for i in doc["items"].values() if i.get("label_status") == "done")


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Label days</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>
body{font:14px -apple-system,system-ui,sans-serif;margin:0 auto;max-width:860px;padding:16px;color:#1d1d1f;background:#fff}
h1{font-size:18px} select,input,button{font:inherit} .thing{border:1px solid #d2d2d7;border-radius:8px;padding:10px 12px;margin:8px 0}
.thing.done{border-color:#0071e3;background:#f5f9ff} .meta{color:#6e6e73;font-size:12px} .lines{font-size:12px;color:#424245;margin:6px 0;white-space:pre-wrap}
.row{display:flex;gap:6px;align-items:center} .row input{flex:1;padding:6px 8px;border:1px solid #d2d2d7;border-radius:6px}
button{padding:6px 10px;border:1px solid #d2d2d7;border-radius:6px;background:#fff;cursor:pointer} .bar{position:sticky;top:0;background:#fff;padding:8px 0;border-bottom:1px solid #d2d2d7}
@media (prefers-color-scheme: dark){body,.bar,button{background:#1d1d1f;color:#f5f5f7}.thing.done{background:#0b2540}.lines{color:#d2d2d7}.row input{background:#2c2c2e;color:#f5f5f7}}
</style></head><body><div class="bar"><h1>Which project was each thing for?</h1>
<div class="row"><select id="day"></select><span id="progress" class="meta"></span></div>
<p class="meta">Type a project path such as <b>LMemM/Resume</b>; use ; for more than one. "Nothing" for an inbox, news, music. Saved as you go.</p></div>
<div id="list"></div><datalist id="names"></datalist><script>
const $=s=>document.querySelector(s); let day=null, things=[], names=new Set();
async function days(){const d=await (await fetch('/api/days')).json(); $('#day').innerHTML=d.map(x=>`<option>${x.day} (${x.done}/${x.total})</option>`).join(''); if(d.length) load(d[0].day);}
async function load(d){day=d; things=await (await fetch('/api/day/'+d)).json(); things.forEach(t=>t.label.forEach(n=>names.add(n))); draw();}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function draw(){ $('#names').innerHTML=[...names].sort().map(n=>`<option value="${esc(n)}">`).join('');
 $('#progress').textContent=`${things.filter(t=>t.status==='done').length} of ${things.length} labelled`;
 $('#list').innerHTML=things.map((t,i)=>`<div class="thing ${t.status}" id="t${i}"><b>${esc(t.title)}</b> <span class="meta">${esc(t.app)} · ${esc(t.kind)} · ${t.times.map(esc).join(', ')}</span>
 <div class="lines">${t.lines.map(esc).join('\\n')}</div><div class="row"><input list="names" value="${esc(t.label.join('; '))}" placeholder="Project/Sub-project" onchange="setLabel(${i},this.value)">
 <button onclick="setLabel(${i},'')">Nothing</button>${i?`<button onclick="setLabel(${i},things[${i-1}].label.join('; '))">Same as above</button>`:''}</div></div>`).join('');}
async function setLabel(i,v){const t=things[i]; t.label=v.split(';').map(s=>s.trim()).filter(Boolean); t.status='done'; t.label.forEach(n=>names.add(n));
 await fetch('/api/day/'+day,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({[t.id]:t.label})}); draw();}
$('#day').onchange=e=>load(e.target.value.slice(0,10)); days();
</script></body></html>"""


def make_handler(folder):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, kind="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _path(self, day):
            return os.path.join(folder, f"{day}.json") if DAY.match(day) else None

        def do_GET(self):
            if self.path == "/":
                return self._send(200, PAGE, "text/html")
            if self.path == "/api/days":
                out = []
                for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
                    if DAY.match(name[:-5]) and name.endswith(".json"):
                        with open(os.path.join(folder, name)) as fh:
                            items = json.load(fh)["items"]
                        out.append({"day": name[:-5], "total": len(items),
                                    "done": sum(1 for i in items.values() if i.get("label_status") == "done")})
                return self._send(200, out)
            if self.path.startswith("/api/day/"):
                path = self._path(self.path.rsplit("/", 1)[1])
                if path and os.path.exists(path):
                    with open(path) as fh:
                        return self._send(200, day_view(json.load(fh)))
            self._send(404, {"error": "not found"})

        def do_POST(self):
            path = self._path(self.path.rsplit("/", 1)[1]) if self.path.startswith("/api/day/") else None
            if not path or not os.path.exists(path):
                return self._send(404, {"error": "not found"})
            labels = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            with open(path) as fh:
                doc = json.load(fh)
            done = apply_labels(doc, labels if isinstance(labels, dict) else {})
            with open(path + ".tmp", "w") as fh:
                json.dump(doc, fh, indent=1, ensure_ascii=False)
            os.replace(path + ".tmp", path)
            self._send(200, {"done": done, "total": len(doc["items"])})

        def log_message(self, *args):
            pass
    return Handler


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", default=None, help="default: <data>/labelled-days")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args(argv)
    folder = args.days or os.path.join(config.paths().data_dir, "labelled-days")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(folder))
    print(f"Labelling {folder}\nOpen http://127.0.0.1:{args.port}/  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
