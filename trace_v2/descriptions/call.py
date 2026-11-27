"""One JSON POST to a local model server that gives up the moment the user is back at the keyboard.

The model work waits until the user has been away (gate.py), but a call that has already started keeps the GPU busy
and the desktop slows down until it ends. `post_json` watches `abort_when` while the call runs and, when it turns
true, cuts the connection (Ollama stops generating when the client goes away) and raises `Interrupted`. Interrupted is a
BaseException on purpose: the passes catch Exception to count a failed attempt, and an interrupted call is not a failure,
so nothing is recorded and the Moment is simply tried again later."""

import http.client
import json
import socket
import threading
import urllib.parse

from .gate import user_active


class Interrupted(BaseException):
    pass


_in_flight = 0
_in_flight_lock = threading.Lock()


def busy():
    """True while any model call is running: it holds the GPU, and a screenshot asked for meanwhile can time out."""
    return _in_flight > 0


def post_json(url, body, timeout, abort_when=user_active, poll_s=1.0):
    global _in_flight
    with _in_flight_lock:
        _in_flight += 1
    try:
        return _post_json(url, body, timeout, abort_when, poll_s)
    finally:
        with _in_flight_lock:
            _in_flight -= 1


def _post_json(url, body, timeout, abort_when, poll_s):
    parts = urllib.parse.urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    state, done = {"aborted": False}, threading.Event()

    def watch():
        while not done.wait(poll_s):
            try:
                active = abort_when() if abort_when else False
            except Exception:                                  # noqa: BLE001 - a broken probe never cancels work
                active = False
            if active:
                state["aborted"] = True
                try:
                    if conn.sock is not None:
                        conn.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return

    watcher = threading.Thread(target=watch, name="model-call-watch", daemon=True)
    watcher.start()
    try:
        conn.request("POST", parts.path or "/", json.dumps(body).encode(), {"Content-Type": "application/json"})
        response = conn.getresponse()
        data = response.read()
    except (OSError, http.client.HTTPException):
        if state["aborted"]:
            raise Interrupted() from None
        raise
    finally:
        done.set()
        conn.close()
    if state["aborted"]:
        raise Interrupted()
    if response.status >= 400:
        raise RuntimeError(f"model server answered HTTP {response.status}")
    return json.loads(data)
