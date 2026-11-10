"""LMemM - the event trail's brain: decides when to look, what the user is on, what changed.

Pure Python. macOS is injected (`front`, `reader`, `vision`), so every rule here runs under
test on any OS. trail_mac.py plugs the real thing in.

Why it is fast: nothing polls the screen. The OS tells us when the app, window, title, focus or
selection changes (Scheduler.poke); a change is read once, after a 60 ms debounce. Most reads are
"light" (a window title, a URL and the focused box: three attribute calls). The full tree walk
only happens to name a chat/place we are unsure of, or to read the text on screen, and the text
read is limited to once per TRAIL_TEXT_EVERY seconds per place.
"""

import re
import time

import config
import observation
import trail_place

CHAT_KINDS = {"chat", "ai_chat"}
SECRET = re.compile(r"\b\d{13,19}\b|\bsk-[A-Za-z0-9_-]{16,}|\b(?:ghp|gho|xox[bap])[-_A-Za-z0-9]{16,}|"
                    r"\b[A-Fa-f0-9]{40,}\b|\b[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/])|password\s*[:=]|\bAKIA[0-9A-Z]{16}\b", re.I)
MAX_ADDED = 40
MAX_PLACES_SEEN = 24


def redact(lines):
    """Drop lines that look like secrets (card numbers, API keys, long tokens, passwords)."""
    return [l for l in lines if l and not SECRET.search(l)]


class Scheduler:
    """When to look. Pokes arrive from OS notifications; `due()` says what to do now."""

    PLACE = {"app", "focus", "title", "selection", "window"}

    def __init__(self, debounce=0.06, max_wait=0.25, text_every=None, poll=None, idle_poll=5.0):
        self.debounce, self.max_wait = debounce, max_wait
        self.text_every = config.TRAIL_TEXT_EVERY if text_every is None else text_every
        self.poll, self.idle_poll = config.TRAIL_POLL if poll is None else poll, idle_poll
        self.first = self.last = None          # pending place pokes
        self.immediate = False
        self.text_pending = False
        self.last_text = None
        self.last_poll = 0.0
        self.idle = False

    def poke(self, kind, now):
        if kind == "app":
            self.immediate = True
        if kind in self.PLACE:
            self.first = now if self.first is None else self.first
            self.last = now
        else:
            self.text_pending = True            # layout, scroll, click, key...

    def set_idle(self, idle):
        self.idle = idle

    def _poll_due(self, now):
        return now - self.last_poll >= (self.idle_poll if self.idle else self.poll)

    def _place_at(self):
        if self.first is None:
            return None
        return min(self.last + self.debounce, self.first + self.max_wait)

    def _text_at(self, now):
        if not self.text_pending:
            return None
        return 0.0 if self.last_text is None else self.last_text + self.text_every

    def due(self, now):
        jobs = set()
        at = self._place_at()
        if self.immediate or (at is not None and now >= at):
            jobs.add("place")
            self.first = self.last = None
            self.immediate = False
        if self._poll_due(now):
            jobs.add("place")
            self.last_poll = now
        t = self._text_at(now)
        if t is not None and now >= t and not self.idle:
            jobs.add("text")
            self.text_pending = False
            self.last_text = now
        return jobs

    def wait(self, now):
        """Seconds the loop may sleep before something is due."""
        times = [self.last_poll + (self.idle_poll if self.idle else self.poll)]
        if self.immediate:
            times.append(now)
        for t in (self._place_at(), self._text_at(now) if not self.idle else None):
            if t is not None:
                times.append(t)
        return max(0.0, min(times) - now)


class Gate:
    """Decides, before anything is read, whether an app or page may be looked at. Fails closed."""

    def __init__(self, watch_apps=None, skip_apps=None, lock_apps=None):
        self.watch = {e.get("id") if isinstance(e, dict) else e for e in (watch_apps or ())} - {None, ""}
        self.skip_apps = config.SKIP_APPS if skip_apps is None else skip_apps
        self.lock_apps = config.LOCK_APPS if lock_apps is None else lock_apps

    def set_watch(self, watch_apps):
        """Change which apps may be looked at while running (Settings > Apps)."""
        self.watch = {e.get("id") if isinstance(e, dict) else e for e in (watch_apps or ())} - {None, ""}

    def app(self, front):
        """Reason to skip this app, or None. Checked first, before any accessibility call."""
        if not front or not front.get("bundle_id"):
            return "unidentified_app"
        if front["bundle_id"] in self.skip_apps:
            return "excluded_app"
        if front["bundle_id"] in self.lock_apps or front.get("app") in self.lock_apps:
            return "locked"
        if self.watch and front["bundle_id"] not in self.watch:
            return "not_in_watch_list"
        return None

    def page(self, url, title, secure_focus=False):
        """Reason to drop what was read (before it is recorded), or None."""
        if secure_focus:
            return "secure_field"
        if title and config.SKIP_TITLES.search(title):
            return "private_or_sensitive_window"
        if url and config.SKIP_SITES.search(url):
            return "excluded_site"
        return None


class Bursts:
    """Typing and scrolling as bursts (counts and timing), never characters."""

    def __init__(self, gap=1.5):
        self.gap = gap
        self.cur = {}

    def feed(self, kind, now, place_key, amount=1, field=None):
        """-> a finished burst of the same kind that this event closed, or None."""
        done = None
        b = self.cur.get(kind)
        if b and (now - b["end"] > self.gap or b["place"] != place_key):
            done, b = self.close(kind), None
        if b is None:
            b = self.cur[kind] = {"start": now, "end": now, "n": 0, "place": place_key, "field": field}
        b["end"], b["n"] = now, b["n"] + amount
        return done

    def close(self, kind):
        b = self.cur.pop(kind, None)
        return None if not b else {"kind": kind, "place": b["place"], "n": b["n"], "ms": int((b["end"] - b["start"]) * 1000),
                                  "field": b["field"]}

    def expired(self, now):
        return [self.close(k) for k in [k for k, b in self.cur.items() if now - b["end"] > self.gap]]

    def all(self):
        return [self.close(k) for k in list(self.cur)]


class Engine:
    def __init__(self, store, front, reader, vision=None, gate=None, clock=None, paused=lambda: False, observations=None):
        self.store, self.front, self.reader, self.vision = store, front, reader, vision
        self.observations = observation.LOG if observations is None else observations   # where the screenshot tracker finds our read
        self.gate = gate or Gate()
        self.clock = clock
        self.wall = time.time               # observations carry wall-clock time, so both readers share one clock
        self.paused = paused
        self.tracker = trail_place.PlaceTracker(config.TRAIL_SETTLE_MS)
        self.bursts = Bursts()
        self.last_bundle = None
        self.last_sig = None
        self.gap = None
        self.seen = {}                      # place key -> set of lines already logged
        self.vision_at = {}                 # place key -> when the screen was last read
        self.cost = {"reads": 0, "full": 0, "ms": [], "vision": 0, "events": 0, "nodes": [], "truncated": 0}

    # ---- helpers

    def now_ms(self, now):
        return int(now * 1000)

    def emit(self, kind, **fields):
        self.cost["events"] += 1
        return self.store.add(kind, **fields)

    def gap_once(self, reason):
        if reason != self.gap:
            self.gap = reason
            self.emit("gap", reason=reason)

    def current_key(self):
        cur = self.tracker.current
        return cur["key"] if cur else None

    # ---- the one entry point

    def step(self, jobs, now):
        """Run one pass for the jobs the scheduler said are due ("place" and/or "text")."""
        if self.paused():
            self.gap_once("paused")
            return
        front = self.front()
        reason = self.gate.app(front)
        if reason:                          # nothing is read from this app, nothing about it is kept
            self.gap_once(reason)
            self.last_sig = None
            self.tracker.current = None
            self.last_bundle = None
            return
        if front["bundle_id"] != self.last_bundle:
            self.emit("app_switch", app=front["app"], bundle_id=front["bundle_id"], from_bundle=self.last_bundle)
            self.last_bundle, self.last_sig = front["bundle_id"], None
            for b in self.bursts.all():
                self.emit_burst(b)
        light = self.reader.read(front, False)
        self.cost["reads"] += 1
        if light is None:
            self.gap_once("no_accessibility")
            return
        reason = self.gate.page(light.url, light.title, light.focus_secure)
        if reason:
            self.gap_once(reason)
            self.last_sig = None
            self.tracker.current = None
            return
        self.gap = None
        sig = (front["pid"], light.title, light.url, light.focus_label)
        changed = sig != self.last_sig
        if not changed and "text" not in jobs:
            return
        self.last_sig = sig
        snap = light
        p = trail_place.place(front["app"], front["bundle_id"], light)
        if "text" in jobs or p["confidence"] < 0.8:
            snap = self.reader.read(front, True) or light
            self.cost["full"] += 1
            self.cost["ms"].append(snap.ms)
            self.cost["nodes"].append(snap.nodes)
            self.cost["truncated"] += snap.truncated
            if snap.focus_secure or self.gate.page(snap.url, snap.title):
                self.gap_once("secure_field")
                return
            p = trail_place.place(front["app"], front["bundle_id"], snap)
        ev = self.tracker.update(p, self.now_ms(now))
        if ev:
            self.emit("focus", app=front["app"], bundle_id=front["bundle_id"], place=_brief(ev),
                      from_place=ev["from_key"], dwell_ms=ev["dwell_ms"])
        if self.tracker.cand is not None:
            self.last_sig = None            # not confirmed yet: read again when the settle time is up
        cur = self.tracker.current
        if cur is not None and "text" in jobs:
            self.read_text(front, cur, snap, now)

    def recheck_in(self, now):
        """Seconds until a waiting (not yet confirmed) place should be read again, or None."""
        ms = self.tracker.pending_ms(self.now_ms(now))
        return None if ms is None else ms / 1000 + 0.01

    # ---- text

    def read_text(self, front, cur, snap, now):
        source, lines, shot = "ax", list(snap.texts), None
        if config.TRAIL_VISION and self.vision and snap.thin() and \
                now - self.vision_at.get(cur["key"], -1e9) >= config.TRAIL_VISION_EVERY:
            self.vision_at[cur["key"]] = now
            shot = self.vision(front, snap)
            self.cost["vision"] += 1
            if shot is None:
                self.gap_once("no_screen_access")
            elif shot.texts:
                source, lines = "ocr", list(shot.texts)
                if shot.headings and not snap.headings:
                    snap.headings = shot.headings
        confs = list(getattr(shot, "confs", None) or []) if source == "ocr" and shot is not None else None
        if confs is not None and len(confs) == len(lines):
            keep = [i for i, l in enumerate(lines) if l and not SECRET.search(l)]
            confs = [confs[i] for i in keep]
        else:
            confs = None
        lines = redact(lines)
        obs = (self.observations.add(observation.from_trail(front, snap, lines, source, self.wall(), confs))
               if config.OBSERVATION_MERGE else None)
        seen = self.seen.setdefault(cur["key"], set())
        added = [l for l in lines if l not in seen][:MAX_ADDED]
        removed = len(seen - set(lines)) if seen else 0
        if len(self.seen) > MAX_PLACES_SEEN:
            self.seen.pop(next(iter(self.seen)))
        seen.clear()
        seen.update(lines)
        if added or removed:
            extra = {"observation": obs.id} if obs else {}
            self.emit("text", place=cur["key"], source=source, added=added, removed=removed, total=len(lines), **extra)

    # ---- input from the event tap

    def on_input(self, kind, now, **fields):
        """kind: key | scroll | click | shortcut. Keys are counted, never recorded."""
        if self.paused() or self.gap in {"secure_field", "excluded_app", "private_or_sensitive_window", "excluded_site"}:
            return
        key = self.current_key()
        if key is None:
            return
        if kind in {"key", "scroll"}:
            field = fields.get("field") or (self.last_sig[3] if self.last_sig else None) or None
            done = self.bursts.feed("typing" if kind == "key" else "scroll", now, key, fields.get("amount", 1), field)
            if done:
                self.emit_burst(done)
        else:
            self.emit(kind, place=key, **fields)

    def flush_bursts(self, now):
        for b in self.bursts.expired(now):
            self.emit_burst(b)

    def emit_burst(self, b):
        if b:
            self.emit(b["kind"], place=b["place"], n=b["n"], ms=b["ms"], field=b["field"])


def _brief(p):
    keys = ("key", "kind", "name", "service", "container", "url", "confidence", "signals", "conflict", "app")
    return {k: p[k] for k in keys if p.get(k) not in (None, "", [])}
