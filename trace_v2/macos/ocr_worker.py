"""The OCR worker process: `python -m trace_v2.macos.ocr_worker --parent-pid N`. Started and supervised by OcrWorkerClient.

It reads framed jobs (screenshot bytes) from stdin, runs the same VisionOCR the in-process reader uses, and writes the
framed result to a private copy of the original stdout. Its own stdout and stderr are pointed at /dev/null, and it logs
nothing: no text, no paths. It never opens the store or the data directory and never receives anything but PNG bytes.

Startup: `hello` at once (the process is alive), then Vision warm-up on an embedded image (a cold first recognition has
taken 66 s), then `ready`. The client sends no job before `ready`.

It exits by itself when the capture process goes away, however that happens (SIGKILL included): stdin reaching EOF ends
the loop, and a watchdog thread notices the parent pid changing even while the main thread is stuck inside Vision.
SIGINT is ignored (the parent decides what a Ctrl-C means); SIGTERM ends it at once.
"""

import argparse
import os
import signal
import sys
import threading
import time
from types import SimpleNamespace

from . import ocr_protocol as proto
from .ocr import READER_VERSION
from trace_v2.observations.models import short_error
from trace_v2.observations.pipeline import ReaderUnavailable

WATCH_PARENT_S = 0.5
INNER_TIMEOUT_S = 24 * 3600.0       # the client enforces the real timeout, and kills this process when it is exceeded


def _watch_parent(parent_pid, interval=WATCH_PARENT_S):
    while True:
        if os.getppid() != parent_pid:
            os._exit(0)
        time.sleep(interval)


def _default_ocr():
    from .ocr import VisionOCR
    return VisionOCR(timeout_s=INNER_TIMEOUT_S)


def _warm_up(ocr):
    from .ocr_warmup_image import PNG
    started = time.monotonic()
    try:
        ocr.read(SimpleNamespace(data=PNG, format="png"), None)
    except BaseException as e:                                  # noqa: BLE001 - reported in `ready`, jobs may still work
        return {"warmup_ok": False, "warmup_error": type(e).__name__, "warmup_s": round(time.monotonic() - started, 2)}
    return {"warmup_ok": True, "warmup_s": round(time.monotonic() - started, 2)}


def handle_job(ocr, header, body, out_fd=None):
    """-> the response header for one job. Same results and the same error classes as an in-process read.
    (A handler may also return raw bytes or a list of dicts/bytes to send; tests use that to misbehave on purpose.)"""
    base = {"job_id": header.get("job_id"), "key": header.get("key")}
    try:
        raw, text = ocr.read(SimpleNamespace(data=body, format=header.get("format")), None)
        return {"type": "result", **base, "raw": raw, "text": text}
    except ReaderUnavailable as e:
        return {"type": "error", **base, "unavailable": True, "error_type": type(e).__name__, "message": short_error(e)}
    except Exception as e:                                       # noqa: BLE001 - recorded by the parent as a failed read
        return {"type": "error", **base, "unavailable": False, "error_type": type(e).__name__, "message": short_error(e)}


def serve(in_fd, out_fd, ocr=None, warm_up=_warm_up, handler=handle_job):
    ocr = ocr or _default_ocr()
    proto.write_all(out_fd, proto.encode({"type": "hello", "pid": os.getpid(), "reader_version": READER_VERSION}))
    proto.write_all(out_fd, proto.encode({"type": "ready", **warm_up(ocr)}))
    while True:
        try:
            header, body = proto.read_frame(in_fd)
        except (proto.EndOfStream, proto.ProtocolError):
            return 0                                             # the parent is gone or the stream is corrupt
        if header["type"] == "bye":
            return 0
        if header["type"] != "job":
            continue
        replies = handler(ocr, header, body, out_fd)
        for reply in replies if isinstance(replies, list) else [replies]:
            proto.write_all(out_fd, reply if isinstance(reply, bytes) else proto.encode(reply))


def main(argv=None, handler=handle_job, ocr=None, warm_up=_warm_up):
    parser = argparse.ArgumentParser(prog="python -m trace_v2.macos.ocr_worker")
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args(argv)
    if os.getppid() != args.parent_pid:
        return 0                                                 # the parent died before we started
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    out_fd = os.dup(1)                                           # the protocol channel; fd 1 and 2 then lead nowhere
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    threading.Thread(target=_watch_parent, args=(args.parent_pid,), name="parent-watch", daemon=True).start()
    try:
        return serve(0, out_fd, ocr=ocr, warm_up=warm_up, handler=handler)
    except (BrokenPipeError, OSError):
        return 0


if __name__ == "__main__":
    sys.exit(main())
