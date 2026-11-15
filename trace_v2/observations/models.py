"""Plain data a capture source hands to the store. Validation lives here; no I/O."""

import json
import math
import re
from dataclasses import dataclass, field

READERS = ("ocr", "accessibility")
READER_STATUSES = ("succeeded", "failed", "unavailable", "skipped", "withheld")
SCREENSHOT_FORMATS = ("png", "jpeg", "heic", "tiff")
MAX_ERROR_CHARS = 500
_KEY = re.compile(r"[A-Za-z0-9:_.\-]{1,200}")


class InvalidCapture(ValueError):
    """A capture draft breaks the contract; nothing was stored."""


def _json_object(name, value):
    if not isinstance(value, dict):
        raise InvalidCapture(f"{name} must be an object")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise InvalidCapture(f"{name} is not JSON-serialisable: {error}") from None


def _ns(name, value, optional=False):
    if value is None and optional:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidCapture(f"{name} must be a non-negative integer of nanoseconds")


def short_error(message):
    return None if message is None else str(message)[:MAX_ERROR_CHARS]


@dataclass(frozen=True)
class Context:
    application: dict          # e.g. {"bundle_id", "name", "pid"}
    window: dict               # e.g. {"title", "id", "bounds"}
    display: dict              # e.g. {"id", "bounds", "scale"}
    error_code: str = None     # set when the context source failed; the dicts are then partial or empty

    def validate(self):
        for name in ("application", "window", "display"):
            _json_object(name, getattr(self, name))


@dataclass(frozen=True)
class ScreenshotCapture:
    data: bytes
    format: str
    width: int
    height: int
    captured_at_ns: int

    def validate(self):
        if not isinstance(self.data, (bytes, bytearray)) or not self.data:
            raise InvalidCapture("screenshot data must be non-empty bytes")
        if self.format not in SCREENSHOT_FORMATS:
            raise InvalidCapture(f"unsupported screenshot format {self.format!r}")
        for name in ("width", "height"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                raise InvalidCapture(f"screenshot {name} must be a positive integer")
        _ns("screenshot captured_at_ns", self.captured_at_ns)


@dataclass(frozen=True)
class ScreenshotFailure:
    error_code: str
    error_message: str = None
    captured_at_ns: int = 0


@dataclass(frozen=True)
class ScreenshotWithheld:
    """No pixels were kept, on purpose (privacy gate or a secret on screen). `reason` says which."""
    reason: str
    captured_at_ns: int = 0


@dataclass(frozen=True)
class ReaderResult:
    reader_version: str
    status: str
    raw: object = None          # the reader's complete output, JSON-serialisable
    text: str = None
    error_code: str = None
    error_message: str = None
    started_at_ns: int = None
    finished_at_ns: int = None

    def validate(self, reader):
        if not isinstance(self.reader_version, str) or not self.reader_version:
            raise InvalidCapture(f"{reader} reader_version is required")
        if self.status not in READER_STATUSES:
            raise InvalidCapture(f"{reader} status must be one of {READER_STATUSES}")
        if self.status == "succeeded" and self.raw is None:
            raise InvalidCapture(f"{reader} succeeded without raw output")
        if self.status != "succeeded" and not self.error_code:
            raise InvalidCapture(f"{reader} status {self.status} needs an error_code")
        if self.text is not None and not isinstance(self.text, str):
            raise InvalidCapture(f"{reader} text must be a string")
        try:
            json.dumps(self.raw, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise InvalidCapture(f"{reader} raw output is not JSON-serialisable: {error}") from None
        _ns(f"{reader} started_at_ns", self.started_at_ns, optional=True)
        _ns(f"{reader} finished_at_ns", self.finished_at_ns, optional=True)


@dataclass(frozen=True)
class InteractionEvent:
    type: str
    occurred_at_ns: int
    payload: dict
    event_id: str = None        # assigned at ingestion when absent


@dataclass(frozen=True)
class CaptureDraft:
    capture_key: str                       # unique per capture attempt, e.g. "<session>:<seq>"
    captured_at_ns: int
    context: Context
    screenshot: object                     # ScreenshotCapture or ScreenshotFailure
    ocr: ReaderResult
    accessibility: ReaderResult
    capture_version: str
    events: tuple = ()                     # InteractionEvent, already sanitised (events.py), arrival order
    events_window: tuple = (None, None)    # (start_ns exclusive, end_ns inclusive)
    event_stats: dict = field(default_factory=dict)
    privacy: dict = field(default_factory=lambda: {"withheld": False})

    def validate(self):
        if not isinstance(self.capture_key, str) or not _KEY.fullmatch(self.capture_key):
            raise InvalidCapture("capture_key must be 1-200 characters of [A-Za-z0-9:_.-]")
        _ns("captured_at_ns", self.captured_at_ns)
        if not isinstance(self.capture_version, str) or not self.capture_version:
            raise InvalidCapture("capture_version is required")
        self.context.validate()
        if isinstance(self.screenshot, ScreenshotCapture):
            self.screenshot.validate()
        elif isinstance(self.screenshot, ScreenshotWithheld):
            if not self.screenshot.reason:
                raise InvalidCapture("a withheld screenshot needs a reason")
        elif not (isinstance(self.screenshot, ScreenshotFailure) and self.screenshot.error_code):
            raise InvalidCapture("screenshot must be a ScreenshotCapture, ScreenshotWithheld or a ScreenshotFailure "
                                 "with an error_code")
        _json_object("privacy", self.privacy)
        if not isinstance(self.privacy.get("withheld"), bool):
            raise InvalidCapture("privacy.withheld must be true or false")
        if self.privacy["withheld"] and (self.events or isinstance(self.screenshot, ScreenshotCapture)
                                         or any(getattr(self, r).raw is not None for r in READERS)):
            raise InvalidCapture("a withheld observation cannot carry a screenshot, reader output or events")
        self.ocr.validate("ocr")
        self.accessibility.validate("accessibility")
        _json_object("event_stats", self.event_stats)
        for e in self.events:
            if not isinstance(e, InteractionEvent):
                raise InvalidCapture("events must be InteractionEvent values")
        start, end = self.events_window
        _ns("events_window start", start, optional=True)
        _ns("events_window end", end, optional=True)


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
