import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from trace_v2 import paths
from trace_v2.observations import ObservationStore
from trace_v2.observations.queries import ScreenshotUnavailable, _decode
from trace_v2.structure.processor import read_connection

PAGE = open(os.path.join(HERE, "viewer.html"), encoding="utf-8").read()


class Data:
    def __init__(self, directory, goals_path):
        self.store = ObservationStore(directory)
        self.conn = read_connection(self.store)
        self.lock = threading.Lock()
        self.goals = json.load(open(goals_path, encoding="utf-8"))

    def moments(self):
        out = []
        for i, m in enumerate(self.goals["moments"]):
            out.append({"index": i, "id": m["id"], "state": m["state"], "start_ns": m["start_ns"], "end_ns": m["end_ns"],
                        "count": len(m["members"]), "prompt": m["prompt"], "members": m["members"],
                        "responses": {k: v["goals"][i] for k, v in self.goals["models"].items()}})
        return out

    def observation(self, oid):
        with self.lock:
            row = self.conn.execute("SELECT captured_at_ns, app_name, window_title FROM observations WHERE observation_id=?",
                                    (oid,)).fetchone()
            if row is None:
                return None
            texts = {}
            for reader in ("ocr", "accessibility"):
                r = self.conn.execute("SELECT status, text, raw_encoding, raw FROM extractions WHERE observation_id=? AND reader=? "
                                      "ORDER BY revision DESC LIMIT 1", (oid, reader)).fetchone()
                if r is None:
                    continue
                text = r[1]
                if not text and r[3] and r[0] == "succeeded":
                    raw = _decode(r[2], r[3]) or {}
                    text = "\n".join(l.get("text", "") for l in raw.get("lines", []) if isinstance(l, dict))
                texts[reader] = {"status": r[0], "text": text or ""}
            shot = self.conn.execute("SELECT status FROM screenshots WHERE observation_id=?", (oid,)).fetchone()
        return {"id": oid, "at_ns": row[0], "app": row[1], "window": row[2], "texts": texts,
                "screenshot": shot[0] if shot else None}

    def screenshot(self, oid):
        try:
            return self.store.screenshot_bytes(oid)
        except (KeyError, ScreenshotUnavailable):
            return None


def make_handler(data):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, kind):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if path == "/api/moments":
                return self._send(200, json.dumps(data.moments()).encode(), "application/json")
            if path.startswith("/api/obs/"):
                found = data.observation(path[len("/api/obs/"):])
                return self._send(200 if found else 404, json.dumps(found or {}).encode(), "application/json")
            if path.startswith("/shot/"):
                blob = data.screenshot(path[len("/shot/"):])
                if blob is None:
                    return self._send(404, b"", "text/plain")
                kind = "image/png" if blob[:4] == b"\x89PNG" else "image/jpeg"
                return self._send(200, blob, kind)
            self._send(404, b"", "text/plain")

        def log_message(self, *args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--goals", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    directory = paths.check_data_dir(os.path.abspath(os.path.expanduser(args.data_dir)))
    data = Data(directory, os.path.expanduser(args.goals))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(data))
    print(f"viewer on http://127.0.0.1:{args.port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        data.conn.close()
        data.store.close()


if __name__ == "__main__":
    main()
