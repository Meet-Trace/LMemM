"""Wire format between the capture process and the OCR worker (trace_v2.macos.ocr_client <-> ocr_worker).

Pure Python, no pyobjc. One frame = 8-byte prefix (">II": header length, body length), a JSON header, a binary body.
Parent -> worker: {"type": "job", "job_id", "key", "format"} + the screenshot bytes; {"type": "bye"}.
Worker -> parent: {"type": "hello", "pid"}, {"type": "ready", "warmup_ok", "warmup_s", ...},
                  {"type": "result", "job_id", "key", "raw", "text"}, {"type": "error", "job_id", "key",
                  "error_type", "message", "unavailable"}.
Every length is bounded, so a corrupt stream is refused instead of read without end.
"""

import json
import os
import struct

PREFIX = struct.Struct(">II")
MAX_HEADER_BYTES = 16 * 1024 * 1024        # a result carries the OCR raw lines
MAX_BODY_BYTES = 128 * 1024 * 1024         # a screenshot; the client refuses to send more than its own, lower, cap


class ProtocolError(Exception):
    """The peer sent something that is not a valid frame."""


class EndOfStream(Exception):
    """The pipe closed (cleanly between frames, or in the middle of one: `partial`)."""

    def __init__(self, partial=False):
        super().__init__("pipe closed mid-frame" if partial else "pipe closed")
        self.partial = partial


def encode_parts(header, body=b""):
    """-> (prefix + header bytes, body): written one after the other, so a screenshot is never copied into a frame."""
    head = json.dumps(header, separators=(",", ":"), allow_nan=False).encode()
    return PREFIX.pack(len(head), len(body)) + head, body


def encode(header, body=b""):
    first, body = encode_parts(header, body)
    return first + bytes(body)


def write_all(fd, data):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _read_exact(fd, n, partial):
    chunks, got = [], 0
    while got < n:
        chunk = os.read(fd, min(1 << 20, n - got))
        if not chunk:
            raise EndOfStream(partial=partial or got > 0)
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def read_frame(fd):
    """-> (header dict, body bytes). Raises EndOfStream or ProtocolError. Blocks until a frame arrives or the pipe closes."""
    head_len, body_len = PREFIX.unpack(_read_exact(fd, PREFIX.size, False))
    if head_len == 0 or head_len > MAX_HEADER_BYTES or body_len > MAX_BODY_BYTES:
        raise ProtocolError(f"frame sizes out of range ({head_len}, {body_len})")
    raw = _read_exact(fd, head_len, True)
    body = _read_exact(fd, body_len, True) if body_len else b""
    try:
        header = json.loads(raw)
    except ValueError as e:
        raise ProtocolError("frame header is not JSON") from e
    if not isinstance(header, dict) or not isinstance(header.get("type"), str):
        raise ProtocolError("frame header has no type")
    return header, body
