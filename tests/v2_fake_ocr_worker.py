"""A fake OCR worker for the client tests: the real ocr_worker loop (framing, parent watch, exit on EOF) with a handler
that does what the screenshot bytes ask. A job's body contains `ACTION:<name>[:<arg>]` tokens, separated by spaces:

    ok (default)      a result whose text is "fake:<sha256 of the body>", so a test can see WHICH image a result is for
    hang              never answer            slow:S    answer after S seconds        crash   SIGKILL itself
    exit              exit(3)                 garbage   write non-frame bytes         truncate  half a frame, then exit
    bigheader         a frame announcing a 1 GB header                                stale   answer for another job id
    stale_then_ok     a stale result, then the right one                              wrongkey  a result with the wrong key, then the right one
    dup               the right result twice                                          unavailable   ReaderUnavailable
    error:Name        a failed read whose class is Name                               balloon:MB    hold MB of memory, then answer
Startup (environment): FAKE_OCR_WARMUP=slow:S|hang|fail   FAKE_OCR_CRASH_FILE=<path>: exit(7) at once while it exists
                       FAKE_OCR_START=garbage: write garbage instead of `hello`
`--tag X` only labels the process so a test can find it with ps."""

import hashlib
import os
import signal
import struct
import sys
import time

from trace_v2.macos import ocr_protocol as proto
from trace_v2.macos import ocr_worker

_held = []


def handler(ocr, header, body, out_fd):
    job, key = header.get("job_id"), header.get("key")
    text = f"fake:{hashlib.sha256(body).hexdigest()}"
    good = {"type": "result", "job_id": job, "key": key, "text": text,
            "raw": {"lines": [{"text": text, "confidence": 1.0, "box": [0, 0, 1, 1]}], "image_size": [1, 1],
                    "recognition_level": "accurate", "languages": ["en_US"], "os_version": "fake"}}
    replies = []
    for token in body.split(b"ACTION:")[1:]:
        name, _, arg = token.split()[0].decode().partition(":")
        if name == "hang":
            time.sleep(10 ** 6)
        elif name == "slow":
            time.sleep(float(arg))
        elif name == "crash":
            os.kill(os.getpid(), signal.SIGKILL)
        elif name == "exit":
            os._exit(3)
        elif name == "garbage":
            return [b"this is not a frame at all" * 4]
        elif name == "truncate":
            os.write(out_fd, proto.encode(good)[:-7])
            os._exit(0)
        elif name == "bigheader":
            return [struct.pack(">II", 1 << 30, 0)]
        elif name == "stale":
            return [{**good, "job_id": job - 1000}]
        elif name == "stale_then_ok":
            replies.append({**good, "job_id": job - 1000})
        elif name == "wrongkey":
            replies.append({**good, "key": "someone-else"})
        elif name == "dup":
            replies.append(good)
        elif name == "unavailable":
            return [{"type": "error", "job_id": job, "key": key, "unavailable": True, "error_type": "ReaderUnavailable",
                     "message": "Vision is not available: fake"}]
        elif name == "error":
            return [{"type": "error", "job_id": job, "key": key, "unavailable": False, "error_type": arg,
                     "message": "fake failure"}]
        elif name == "balloon":
            _held.append(bytearray(b"x" * (int(arg) * 1024 * 1024)))
    return replies + [good]


def warm_up(ocr):
    mode = os.environ.get("FAKE_OCR_WARMUP", "")
    if mode.startswith("slow:"):
        time.sleep(float(mode[5:]))
    elif mode == "hang":
        time.sleep(10 ** 6)
    return {"warmup_ok": mode != "fail", "warmup_s": 0.0}


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--tag" in argv:
        i = argv.index("--tag")
        del argv[i:i + 2]
    crash_file = os.environ.get("FAKE_OCR_CRASH_FILE")
    if crash_file and os.path.exists(crash_file):
        os._exit(7)
    if os.environ.get("FAKE_OCR_START") == "garbage":
        os.write(1, b"garbage-before-hello" * 8)
        time.sleep(30)
    sys.exit(ocr_worker.main(argv, handler=handler, ocr=object(), warm_up=warm_up))
