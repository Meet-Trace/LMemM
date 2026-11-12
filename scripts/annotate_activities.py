#!/usr/bin/env python3
"""Activity annotation tool: review one recorded day as a few dozen cards. Local only, standard library only.

    python3 scripts/annotate_activities.py serve  DATASET_DIR ANNOT_DIR [--port 8765]
    python3 scripts/annotate_activities.py propose DATASET_DIR [--private]        (print the cards, write nothing)
    python3 scripts/annotate_activities.py export DATASET_DIR ANNOT_DIR           (activities.json, provenance.json, dataset/)

DATASET_DIR holds moments/*.jsonl (it is only READ). ANNOT_DIR is a new folder outside the repo where your decisions go.
Then score the engine against your annotation with the existing command:
    python3 scripts/eval_activities.py replay --dataset ANNOT_DIR/dataset --out OUT_DIR
The server listens on 127.0.0.1 only and needs the secret in the printed link. Nothing is uploaded. No inference runs."""
import argparse
import http.server
import json
import os
import secrets
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import activity_annotation as aa  # noqa: E402

PAGE = os.path.join(HERE, "annotate_page.html")


def hms(t):
    return time.strftime("%H:%M:%S", time.localtime(t))


def cmd_propose(a):
    p = aa.propose(aa.read_records(a.dataset))
    print(f"visits {p['n_visits']}  stretches {p['n_stretches']}  cards {len(p['blocks'])}")
    for b in p["blocks"]:
        print(f"{b['id']} {b['kind']:6s} {hms(b['t0'])}-{hms(b['t1'])} {len(b['visits']):3d} visits {b['dwell']:5.0f}s "
              f"{','.join(b['apps'])}" + ("" if a.private else "  " + (p["visits"][b["visits"][0]]["title"] or "")[:50]))
        for s in b["suggest"]:
            print(f"      maybe {s['block']}: {s['why']} ({s['gap_s'] // 60} min apart)")


def cmd_export(a):
    r = aa.export(a.annot, a.dataset)
    print(f"exported {r['activities']} activities; moments by status {r['moments_by_status']}")
    print(f"scoring dataset: {r['dataset']}")


def cmd_serve(a):
    proposal, created = aa.init_folder(a.annot, a.dataset)
    token = secrets.token_urlsafe(16)
    print(("created " if created else "resumed ") + a.annot, f"({len(proposal['blocks'])} cards, {proposal['n_visits']} visits)")

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *x):
            pass

        def _ok(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _state(self):
            return aa.replay(proposal, aa.read_events(a.annot))

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if not self._ok() or (q.get("k") or [""])[0] != token:
                return self._send(403, "forbidden", "text/plain")
            if u.path == "/":
                with open(PAGE, encoding="utf-8") as fh:
                    return self._send(200, fh.read().replace("__TOKEN__", token), "text/html")
            if u.path == "/api/state":
                return self._send(200, json.dumps(aa.view(proposal, self._state())))
            self._send(404, "not found", "text/plain")

        def do_POST(self):
            if not self._ok() or self.headers.get("X-Token") != token:
                return self._send(403, "forbidden", "text/plain")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                ev_ = json.loads(self.rfile.read(n))
                if self.path == "/api/event":
                    aa.append_event(a.annot, proposal, ev_)
                elif self.path == "/api/export":
                    r = aa.export(a.annot, a.dataset)
                    return self._send(200, json.dumps(r))
                else:
                    return self._send(404, "not found", "text/plain")
                self._send(200, json.dumps(aa.view(proposal, self._state())))
            except (aa.AnnotationError, ValueError, KeyError) as e:
                self._send(400, json.dumps({"error": str(e)}))

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), H)
    url = f"http://127.0.0.1:{a.port}/?k={token}"
    print("open this link in your browser (Ctrl-C to stop):\n  " + url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped; your decisions are in " + os.path.join(a.annot, "events.jsonl"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("dataset")
    p.add_argument("--private", action="store_true")
    p.set_defaults(fn=cmd_propose)
    for name, fn in (("serve", cmd_serve), ("export", cmd_export)):
        s = sub.add_parser(name)
        s.add_argument("dataset")
        s.add_argument("annot")
        if name == "serve":
            s.add_argument("--port", type=int, default=8765)
        s.set_defaults(fn=fn)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
