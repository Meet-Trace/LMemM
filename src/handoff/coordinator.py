"""The handoff workflow: capture, prepare, confirm, transfer. Pure Python; adapters do the I/O.

States:  idle -> capturing -> ready -> prepared -> (review) -> transferring -> done | failed
         any state -> cancelled.  A second `invoke()` while a session is open returns the same
         session, so repeated hotkey presses never stack windows.

Rules the tests pin down:
 * a destination is only ever asked to open and insert; nothing here can submit a prompt;
 * cancelling or failing never loses the prepared handoff (`session.handoff` stays readable);
 * success is reported as `inserted` only when the adapter verified the text landed, otherwise
   `clipboard` (copied, paste it yourself) or `failed`;
 * logs and the status record hold metadata only, never conversation text.
"""

import threading
import time
from dataclasses import dataclass, field

from .adapters import CLIPBOARD, INSERT, SourceInfo
from .generator import RuleGenerator
from .model import (COMPLETE, DENIED, EMPTY, FAILED, PARTIAL, UNSUPPORTED, Conversation, Handoff)

IDLE, CAPTURING, READY, PREPARED, TRANSFERRING, DONE, FAILED_STATE, CANCELLED = (
    "idle", "capturing", "ready", "prepared", "transferring", "done", "failed", "cancelled")

EXTRACT_TIMEOUT = 4.0
DEFAULT_MAX_CHARS = 6000
MAX_CHARS = {"chatgpt": 24000, "claude": 24000, "gemini": 24000, "cursor": 12000}      # conservative defaults

FALLBACK_COPY = "Copy the conversation, then choose a destination."


@dataclass
class Result:
    """What happened to the transfer. `method` is how it reached the destination, or ''."""
    ok: bool
    method: str = ""                 # "inserted" | "clipboard" | ""
    message: str = ""
    reason: str = ""


@dataclass
class Record:
    """Metadata for one handoff: stages, timings, outcome. Never holds conversation text."""
    source_detection: str = ""
    extraction: str = ""
    generation: str = ""
    destination: str = ""
    transfer_method: str = ""
    final: str = ""
    failure: str = ""
    timings_ms: dict = field(default_factory=dict)


class Session:
    def __init__(self):
        self.state = IDLE
        self.info = SourceInfo()
        self.source = None            # adapter
        self.conversation = None      # Conversation
        self.handoff = None           # Handoff; survives failure and cancellation
        self.destination = None
        self.result = None
        self.record = Record()
        self.fallback = ""            # UI text when the source is unsupported / unreadable
        self.edited_text = None       # the user's edits to the outgoing text only

    @property
    def text(self):
        if self.edited_text is not None:
            return self.edited_text
        return self.handoff.text if self.handoff else ""

    @property
    def active(self):
        return self.state not in (IDLE, DONE, FAILED_STATE, CANCELLED)


class Coordinator:
    def __init__(self, sources, destinations, clipboard, front, generator=None, clock=time.monotonic,
                 log=None, extract_timeout=EXTRACT_TIMEOUT, is_private=lambda info: False):
        self.sources, self.destinations, self.clipboard = list(sources), list(destinations), clipboard
        self.front = front                        # () -> SourceInfo of the app in front
        self.generator = generator or RuleGenerator()
        self.clock, self.extract_timeout = clock, extract_timeout
        self.log = log or (lambda record: None)
        self.is_private = is_private              # a private app/site/window is never read
        self.session = None
        self._recent = []                         # destination ids, most recent first
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- destinations

    def destination_list(self):
        """Destinations except the source (unless nothing else exists), recent first, with status."""
        src_id = self.session.source.id if self.session and self.session.source else None
        src_app = (self.session.info.app if self.session else "").lower()
        rows = []
        for d in self.destinations:
            if d.id == src_id or d.name.lower() == src_app:
                continue
            ok, why = d.available()
            rows.append({"id": d.id, "name": d.name, "available": ok, "reason": why,
                         "method": INSERT if d.capabilities.can_insert else CLIPBOARD})
        rank = {i: n for n, i in enumerate(self._recent)}
        rows.sort(key=lambda r: (rank.get(r["id"], len(rank)), r["name"]))
        return rows

    def _destination(self, dest_id):
        for d in self.destinations:
            if d.id == dest_id:
                return d
        raise KeyError(dest_id)

    # ---------------------------------------------------------------- workflow

    def invoke(self):
        """The hotkey. Returns the session; an open one is reused. Reading the front app happens
        here, on the caller's thread, before any LMemM window takes focus."""
        with self._lock:
            if self.session and self.session.active:
                return self.session
            s = self.session = Session()
        t0 = self.clock()
        s.state = CAPTURING
        try:
            s.info = self.front() or SourceInfo()
        except Exception:
            s.info = SourceInfo()
        s.private = False
        try:
            s.private = bool(self.is_private(s.info))
        except Exception:
            s.private = True
        for a in (() if s.private else self.sources):
            if a.matches(s.info):
                s.source = a
                break
        s.record.source_detection = s.source.id if s.source else "none"
        s.record.timings_ms["detect"] = self._ms(t0)
        return s

    def capture(self, s=None):
        """Run the source adapter (bounded by a timeout). Safe to call from a worker thread."""
        s = s or self.session
        if s is None or s.state != CAPTURING:
            return s
        t0 = self.clock()
        if s.source is None:
            why = ("This is a private app or window, so LMemM didn't read it." if getattr(s, "private", False)
                   else "LMemM couldn't identify a supported conversation here.")
            s.conversation = Conversation("none", s.info.app, status=UNSUPPORTED, reason=why)
        else:
            s.conversation = self._extract(s)
        c = s.conversation
        s.record.extraction = c.status
        s.record.timings_ms["extract"] = self._ms(t0)
        if s.state == CANCELLED:
            return s
        if c.readable:
            s.state = READY
        else:
            s.fallback = (c.reason or "LMemM couldn't read this conversation.") + " " + FALLBACK_COPY
            s.state = READY             # the panel still opens, with the fallback and the copy path
        return s

    def _extract(self, s):
        box = {}

        def run():
            try:
                box["conv"] = s.source.extract(s.info, cancelled=lambda: s.state == CANCELLED)
            except Exception as e:
                box["conv"] = Conversation(s.source.id, s.info.app, status=FAILED,
                                           reason=f"Reading failed ({type(e).__name__}).")
        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(self.extract_timeout)
        if th.is_alive():
            return Conversation(s.source.id, s.info.app, status=FAILED,
                                reason="Reading the conversation took too long.")
        return box["conv"]

    def use_manual(self, source, s=None):
        """Replace the conversation with what a manual source (the clipboard) provides."""
        s = s or self.session
        s.source = source
        s.state = CAPTURING
        self.capture(s)
        return s

    def choose(self, dest_id, s=None):
        """Pick a destination and build the package. A failure leaves the session usable."""
        s = s or self.session
        if s is None or s.state not in (READY, PREPARED):
            return s
        dest = self._destination(dest_id)
        if not (s.conversation and s.conversation.readable):
            s.result = Result(False, reason=s.fallback or "Nothing to hand off.",
                              message=s.fallback or "Nothing to hand off.")
            return s
        t0 = self.clock()
        limit = MAX_CHARS.get(dest.id.split("-")[-1], DEFAULT_MAX_CHARS)
        try:
            s.handoff = self.generator.generate(s.conversation, dest.name, limit)
            s.record.generation = "ok"
        except Exception as e:
            s.record.generation = "failed"
            s.record.failure = type(e).__name__
            s.result = Result(False, reason=str(e), message="Couldn't prepare the handoff.")
            return s
        s.record.timings_ms["generate"] = self._ms(t0)
        s.destination, s.edited_text, s.state = dest, None, PREPARED
        s.record.destination = dest.id
        return s

    def edit(self, text, s=None):
        """The user's edits change the outgoing text only; nothing else is touched."""
        s = s or self.session
        if s and s.state == PREPARED:
            s.edited_text = text
        return s

    def back_to_destinations(self, s=None):
        s = s or self.session
        if s and s.state == PREPARED:
            s.state, s.destination = READY, None
        return s

    def transfer(self, s=None):
        """The user pressed Continue. Insert if the destination can, otherwise (or if it can't
        prove it worked) copy to the clipboard. Never sends."""
        s = s or self.session
        if s is None or s.state != PREPARED:
            return s
        s.state = TRANSFERRING
        t0 = self.clock()
        dest, text = s.destination, s.text
        ok, why = dest.available()
        if not ok:
            return self._finish(s, t0, self._clipboard_result(s, text, dest, why or f"{dest.name} is unavailable."))
        try:
            opened = dest.open()
        except Exception:
            opened = False
        if not opened:
            return self._finish(s, t0, self._clipboard_result(s, text, dest, f"Couldn't open {dest.name}."))
        if dest.capabilities.can_insert:
            try:
                inserted = bool(dest.insert(text))
            except Exception:
                inserted = False
            if inserted:
                s.record.transfer_method = "inserted"
                return self._finish(s, t0, Result(True, "inserted", f"Context ready in {dest.name}."))
        return self._finish(s, t0, self._clipboard_result(s, text, dest, "", opened=True))

    def _clipboard_result(self, s, text, dest, why, opened=False):
        try:
            copied = self.clipboard.write(text) is not False
        except Exception:
            copied = False
        if copied and (opened or not why):
            s.record.transfer_method = "clipboard"
            return Result(True, "clipboard", f"Context copied. Paste it into {dest.name} to continue.")
        if copied:
            s.record.transfer_method = "clipboard"
            return Result(False, "clipboard", f"Couldn't open {dest.name}. Your handoff is copied; paste it there.", why)
        return Result(False, "", f"Couldn't transfer the context to {dest.name}. Your handoff is still available to copy.", why)

    def _finish(self, s, t0, result):
        s.result = result
        s.record.timings_ms["transfer"] = self._ms(t0)
        s.record.final = "ok" if result.ok else "failed"
        s.record.failure = result.reason if not result.ok else ""
        s.state = DONE if result.ok else FAILED_STATE
        if s.destination is not None:
            self._recent = [s.destination.id] + [i for i in self._recent if i != s.destination.id]
        self.log(s.record)
        return s

    def copy(self, s=None):
        """Copy the outgoing text without transferring (Review view)."""
        s = s or self.session
        return bool(s and s.handoff and self.clipboard.write(s.text) is not False)

    def cancel(self, s=None):
        """Dismiss. Nothing is sent or copied. The handoff stays on the session until cleared."""
        s = s or self.session
        if s and s.active:
            s.state = CANCELLED
            s.record.final = "cancelled"
            self.log(s.record)
        return s

    def clear(self):
        """Drop the current handoff and everything captured for it."""
        self.session = None

    def _ms(self, t0):
        return round((self.clock() - t0) * 1000, 1)
