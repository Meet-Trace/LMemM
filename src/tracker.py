#!/usr/bin/env python3
"""
LMemM - the orchestrator. Decides when to take a screenshot, hands it to the
resolver thread, and turns resolved screens into memory and a timeline.

    watch    app switch (macOS notification), window / tab title change, new front
             window, lock / unlock, sleep / wake, display sleep      (macos.py)
    trigger  wait SETTLE s for the screen to settle (debounce)       -> capture
    timer    same window, nothing triggered, every EVERY s           -> capture
    pause    idle > IDLE s, locked, asleep, `lmemm.py pause`, skip-listed app/site
    capture  screenshot of the display the window is on              -> queue
    resolve  (own thread) OCR -> what you're doing -> which thing    (resolver,
             understand, identity) -> activity since its last frame (activity)
             -> memory entry + timeline (store); notes attach (notes)

Run it through lmemm.py:   python3 lmemm.py          (Ctrl-C to stop)
"""

import hashlib
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import objc
import ax
from AppKit import NSEvent

import activity
import apps
import config
import dictation
import identity
import input_monitor as input_hooks
import macos
import menu_model
import menubar
import window as main_window
import window_model
import notes
import permissions
import onboarding
import resolver
import retention
import rules
import settings_model
import store
import suggestions
import thing_actions
import understand
import widget
from input_events import Aggregator
from input_store import InputStore, private_write
from memory_content import extract_content, remember_content, visible_window_region


CHATTY = {"chat", "chat_list", "ai_conversation"}    # text can arrive here with no input from you


def hms(ts):
    return f"{ts[9:11]}:{ts[11:13]}:{ts[13:15]}"


def now_hms():
    return datetime.now().strftime("%H:%M:%S")


def say(msg):
    print(msg, flush=True)


def line(t, app, msg):
    """Every live line looks the same: time, app, what happened."""
    say(f"{t}  {(app or '')[:14]:14}  {msg}")


class Tracker:
    def __init__(self, every=config.EVERY, input_apps=None, input_retention_hours=24, show_widget=True, trail=True):
        p = config.paths()
        self.every = every
        self.session = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.items = store.load_items()      # the memory: one entry per thing, across sessions
        self.events = []                     # this session's timeline, pointing at items
        self.notes = []                      # this session's notes
        self.now = None                      # the quick answer to "what am I on?" (see identify_now)
        self.quick_q = queue.Queue()
        self.ocr_apps = {}                   # app -> metadata, for apps whose place can only be told by reading the screen
        self.last_read = 0.0
        self.nsapp = None
        self.ax_now = None                   # the app-reported place (ax.py), when it names a known item
        self.quick_seq = 0
        self.frame_item = {}                 # capture ts -> memory item it became
        self.lock = threading.Lock()         # the resolver thread and the note window both write memory
        self.q = queue.Queue()
        self.stats = Counter()
        self.running = True

        # capture scheduling
        self.flags = {"asleep": False, "display_off": False, "locked": False}
        self.pending = None                  # (trigger, due, first_seen)
        self.pin = False
        self.note_request = False
        self.waiting = []                    # saved notes whose screen is still being read
        self.skipped_place = None            # (app, window) of the last window we chose not to read
        self.skip_kind = None                # why: "private" (a private window, a password manager...) or "unwatched" (R11)
        user = store.load_user() or {}
        self.watch_apps = apps.clean(user.get("watch_apps"))     # empty: every app (apps.py)
        config.set_skip_apps(e["id"] for e in apps.clean(user.get("skip_apps")))    # Settings > Privacy
        config.SCREENSHOT_DAYS = settings_model.keep_days(user)                     # Settings > General
        self.screen_ok, self.screen_checked = True, 0.0
        self.cur_place = None                # (app, window) of the newest timeline event
        self.pill_place = None               # (app, window) the pill last refreshed for
        self.paused = None                   # automatic pause reason (idle, locked, ...)
        self.manual_paused = False           # `lmemm.py pause`, or the pill's Pause
        self.pause_until = None              # epoch seconds when a timed pause ends (None: until you resume)
        self.last_sig = None
        self.last_capture = 0.0
        self.last_poll = 0.0
        self.last_frame = None               # previous frame (pixels + OCR) for change tracking
        self.frames = {}                     # capture ts -> macos.Frame, in memory until resolved
        self.idle_steps = 0                  # consecutive unchanged frames: drives the timer back-off
        self.interval_cap = config.BACKOFF_CAP
        self.save_interval = 0               # seconds between memory writes; run() sets SAVE_EVERY
        self.last_save = 0.0
        self.dirty = False
        self.metrics = Counter()             # work done and time spent, shown by `status`
        self.started = time.time()
        self.cpu_started = time.process_time()
        self.resources = {}
        self.last_resource_sample = 0.0
        self.last_retention_sweep = 0.0
        self.panel = dictation.NotePanel()
        self.show_widget = show_widget
        self.use_trail = trail               # the accessibility event trail (trail_mac.py) runs inside this process
        self.trail = None
        self.widget = None                   # the on-screen pill (widget.py), made in run()
        self.menubar = None                  # the menu-bar item (menubar.py), made in run()
        self.memory_client = None
        self.memory_events = []
        self.memory_controller = None
        self.main_window = None              # the window (window.py), made the first time it is opened
        self.page_data_said = False
        self.menu_refreshed = 0.0
        self.menu_reported = False
        self.started_at = time.time()
        self.quit_requested = False          # "Quit LMemM" in the menu: ends the main loop
        self.after_exit = None               # what to do once stopped: "restart", "setup" or "delete" (hand_over)
        self.screen_restart, self.restart_checked = False, 0.0   # screen access is on for a new run, not this one
        self.suggestion = None               # a group offered as one project: {name, ids, reason}
        self.suggest_answers = self.load_answers()
        self.sug = self.load_sug()           # proposals waiting for an answer (suggestions.py): the pill and the window share them
        self.last_finder, self.finder_busy = 0.0, False   # project_finder.py: last look for projects, and one running now
        self.last_widget_refresh = 0.0
        self.control_file = Path(p.control_file)
        self.status_file = Path(p.status_file)

        # opt-in input timeline
        self.origin_ns = time.monotonic_ns()
        self.input_aggregator = self.input_monitor = self.input_store = None
        self.input_context = None
        self.last_input_status = None
        self.last_input_prune = 0.0
        if input_apps:
            self.input_aggregator = Aggregator(self.session, datetime.now(timezone.utc).isoformat(), self.origin_ns)
            self.input_monitor = input_hooks.InputMonitor(self.input_aggregator, set(input_apps))
            self.input_store = InputStore(self.session, p.memory_dir, input_retention_hours)
            self.input_store.initialize_baseline(self.items)
        elif (Path(p.memory_dir) / "contributions" / "baseline.json").exists():
            # Once provenance starts, later capture-only sessions remain reconstructable.
            self.input_store = InputStore(self.session, p.memory_dir)
            self.input_store.initialize_baseline(self.items)

    # ------------------------------------------------------------ controls

    def close_interval(self):
        """Whatever comes next starts a new stretch of time (pause, lock, note window)."""
        if self.events and not self.events[-1].get("_closed"):
            self.events[-1]["_closed"] = True
        self.last_frame = None

    def set_manual_pause(self, paused, until=None):
        self.manual_paused = paused
        if self.memory_client and (paused or not any(self.flags.values())):
            self.memory_client.request("pause" if paused else "resume", {}, "lifecycle")
        self.pause_until = until if paused else None
        self.pending = None
        self.pin = False
        self.note_request = False
        self.close_interval()
        if self.panel.open:
            self.panel.close(save=False)
        if self.input_monitor:
            self.input_monitor.set_paused(paused)
            if not paused and self.input_monitor.state in {"disabled", "unavailable"}:
                self.input_monitor.stop()
                self.input_monitor.start(request_permission=False)
        if self.input_store:
            with self.lock:
                self.input_store.invalidate_context("paused" if paused else "resumed")
        if not paused:
            self.trigger("resume")
        self.publish_status()

    def process_control(self):
        """Apply a command written by `lmemm.py pause|resume|...` for this process."""
        if not self.control_file.exists():
            return
        try:
            doc = json.loads(self.control_file.read_text())
            if doc.get("pid") == os.getpid():
                self.apply_control(doc)
        except (OSError, ValueError, TypeError):
            say("Ignored invalid tracker control file")
        finally:
            self.control_file.unlink(missing_ok=True)

    def apply_control(self, doc):
        if doc.get("action") == "open":
            self.open_window()
        elif doc.get("action") in {"pause", "resume"}:
            self.set_manual_pause(doc["action"] == "pause")
        elif doc.get("action") == "notes_done" and isinstance(doc.get("ids"), list):
            with self.lock:
                found = notes.set_done(self.items, doc["ids"], done=bool(doc.get("done", True)))
                if found:
                    self.save(force=True)
            line(now_hms(), "", f"{len(found)} note{'s' * (len(found) != 1)} marked "
                 + ("done" if doc.get("done", True) else "open"))

        elif doc.get("action") == "suggest":
            self.offer_project(doc.get("name"), doc.get("ids") or [], doc.get("reason", ""))
        elif doc.get("action") == "look_for_projects":
            if not self.finder_busy:
                self.last_finder, self.finder_busy = time.time(), True
                threading.Thread(target=self.finder_pass, daemon=True).start()
        elif doc.get("action") == "suggest_for":
            self.offer_items(doc.get("project", ""), doc.get("ids") or [], doc.get("reason", ""))

    def publish_status(self):
        private_write(self.status_file, {
            "pid": os.getpid(), "session": self.session, "paused": self.manual_paused,
            "input": self.input_monitor.status() if self.input_monitor else {"state": "off"},
            "cost": self.cost_report()})

    # ------------------------------------------------------------ what it costs

    def timed(self, name, started):
        """Add the time since `started` (perf_counter) to the running totals for `name`."""
        self.metrics[name + "_ms"] += (time.perf_counter() - started) * 1000
        self.metrics[name + "_n"] += 1

    def sample_resources(self, now):
        """Memory and CPU of this process, every 30 s. Warns when either looks wrong."""
        if now - self.last_resource_sample < 30:
            return
        wall = now - (self.last_resource_sample or self.started)
        cpu = time.process_time()
        last_cpu = self.resources.get("_cpu", self.cpu_started)
        self.last_resource_sample = now
        try:
            rss = int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())],
                                              timeout=2)) / 1024
        except (OSError, ValueError, subprocess.SubprocessError):
            rss = self.resources.get("rss_mb", 0)
        recent = 100 * (cpu - last_cpu) / max(wall, 1)
        self.resources = {"rss_mb": round(rss), "peak_rss_mb": round(max(rss, self.resources.get("peak_rss_mb", 0))),
                          "cpu_pct_recent": round(recent), "_cpu": cpu,
                          "cpu_pct_avg": round(100 * (cpu - self.cpu_started) / max(now - self.started, 1))}
        # warn about memory, or CPU that stays high for two readings in a row (startup is busy)
        high_cpu = recent > config.CPU_WARN_PCT
        sustained = high_cpu and self.resources.get("_high_cpu", False)
        self.resources["_high_cpu"] = high_cpu
        if now - self.started > 60 and (rss > config.RSS_WARN_MB or sustained):
            line(now_hms(), "", f"⚠ LMemM is using {round(rss)} MB and {round(recent)}% of a core")
        self.publish_status()

    def cost_report(self):
        m = self.metrics

        def avg(name):
            return round(m[name + "_ms"] / m[name + "_n"]) if m[name + "_n"] else None
        return {
            "running_for": store.duration(time.time() - self.started),
            "memory_mb": self.resources.get("rss_mb"), "peak_memory_mb": self.resources.get("peak_rss_mb"),
            "cpu_pct_now": self.resources.get("cpu_pct_recent"), "cpu_pct_average": self.resources.get("cpu_pct_avg"),
            "captures": self.stats["captured"], "skipped_ocr_unchanged": self.stats["no_ocr"],
            "ocr": {"fast": m["ocr_fast_n"], "accurate": m["ocr_accurate_n"], "redone_accurate": m["ocr_redo_n"]},
            "avg_ms": {"capture": avg("capture"), "ocr_fast": avg("ocr_fast"), "ocr_accurate": avg("ocr_accurate"),
                       "handle": avg("handle"), "save": avg("save")},
            "capture_interval_s": round(self.current_interval(time.time(), quiet=True)),
        }

    # ------------------------------------------------------------ input timeline

    def poll_input(self):
        if self.input_store and time.monotonic() - self.last_input_prune >= 60:
            with self.lock:
                self.input_store.read()
            self.last_input_prune = time.monotonic()
        if self.input_monitor is None:
            return
        monitor = self.input_monitor
        blocked = self.manual_paused or any(self.flags.values()) or self.panel.open
        if not blocked and monitor.state == "recording":
            monitor.prepare_context()
        context = None if blocked or monitor.state != "recording" else monitor.context_provider(monitor.allowed_apps)
        if monitor.state != "recording" and not blocked:
            self.input_context = None
        elif blocked or context is None:
            prior = self.input_context
            monitor.set_paused(True)
            monitor._gap("paused" if blocked else monitor.context_denial or "protected_or_excluded")
            if prior and not blocked:
                foreground = macos.front()
                if foreground and foreground.get("bundle_id") not in monitor.allowed_apps:
                    ns = time.monotonic_ns()
                    self.input_aggregator._record("context_exit", ns, ns, {"from": prior["id"]}, prior["id"])
            self.input_context = None
        else:
            if self.input_context and self.input_context["id"] != context["id"]:
                previous = self.input_context["id"]
                self.input_aggregator.clear("context_change")
                ns = time.monotonic_ns()
                self.input_aggregator._record("context_transition", ns, ns,
                                              {"from": previous, "to": context["id"]}, context["id"])
                with self.lock:
                    self.input_store.invalidate_context("context_change")
            monitor.set_paused(False)
            self.input_context = context
        monitor.verify_context(context)
        summaries = self.input_aggregator.drain(time.monotonic_ns())
        with self.lock:
            if summaries:
                self.input_store.append(summaries)
                self.input_store.confirm_navigation(summaries)
            status = monitor.status()
            if status != self.last_input_status:
                self.input_store.set_status(status)
                if status.get("gap"):
                    self.input_store.invalidate_context(status["gap"])
                self.last_input_status = status
                self.publish_status()
        if (not blocked and context and monitor.state == "recording"
                and any(e["kind"] in {"keyboard_activity", "click", "scroll", "navigation_shortcut"} for e in summaries)):
            self.trigger("input_activity")

    # ------------------------------------------------------------ scheduling

    def trigger(self, why):
        self.idle_steps = 0                  # something happened: back to the fast timer
        if self.input_monitor and why in {"app_switch", "window_change", "tab_change"}:
            self.input_monitor.invalidate_boundary()
        now = time.time()
        if self.pending:
            first = self.pending[2]
            due = min(now + config.SETTLE, first + config.MAX_SETTLE)
            self.pending = (self.pending[0] if self.pending[0] == "app_switch" else why, due, first)
        else:
            self.pending = (why, now + config.SETTLE, now)

    def set_flag(self, k, v):
        self.flags[k] = v
        if v and self.memory_client:
            self.memory_client.request("pause", {"reason": "System " + k}, "system-pause")
        if v:
            self.close_interval()
            if self.input_monitor:
                self.input_monitor.set_paused(True)
        else:
            self.trigger("wake")

    def tick(self):
        """One pass of the main loop (~4x a second)."""
        if self.quit_requested:
            raise KeyboardInterrupt
        self.process_control()
        if self.memory_client:
            events=self.memory_client.poll()
            if self.memory_controller:
                self.memory_controller.poll(events)
            else:self.memory_events.extend(events)
        self.poll_input()
        self.maintain()
        if self.widget:
            here = self.front_sig()
            if here != self.pill_place:             # you switched tab or app: update the pill now, not within a second
                self.pill_place = here
                self.last_widget_refresh = 0.0
        if self.widget and time.time() - self.last_widget_refresh >= 1:
            self.last_widget_refresh = time.time()
            self.widget.refresh()
        if self.menubar and time.time() - self.menu_refreshed >= 1:
            self.menu_refreshed = time.time()
            if not self.menu_reported and time.time() - self.started_at > 4:
                self.menu_reported = True               # after the run loop has laid the bar out
                say(f"Menu-bar item, 4s later: {self.menubar.where()}")
            status = self.widget_status()
            self.menubar.update(menu_model.view(status, self.watch_apps, hotkey=dictation.HOTKEY_LABEL))
            if self.main_window and self.main_window.visible():
                self.main_window.update(self.window_view(status))
        if self.main_window:
            self.main_window.tick()                     # a ticked note's hold and the Undo line follow the clock
        if self.widget:
            self.widget.pulse(self.widget_heard())      # waveform + words while the note window is open
        if self.manual_paused and self.pause_until and time.time() >= self.pause_until:
            self.set_manual_pause(False)                # a timed pause ends by itself
            line(now_hms(), "", "resumed (pause ended)")
            if self.widget:
                self.widget.flash("Remembering again")
        if self.note_request and self.manual_paused:
            self.note_request = False                   # R9: say why, with a way out, instead of nothing
            if self.widget:
                self.widget.show_paused()
        if self.manual_paused:
            return
        if any(self.flags.values()):
            self.note_request = False
            if self.panel.open:
                self.panel.close(save=False)
        if self.panel.open:
            self.note_request = False                  # R3: already open, a second press does nothing
            self.close_interval()
            self.panel.poll()           # show what you've said so far
            return                      # don't capture the note window itself
        if self.note_request:
            self.note_request = False
            card_open = bool(self.widget and self.widget.card_open)
            front = macos.front()
            unwatched = bool(front) and not apps.watched(self.watch_apps, front["bundle_id"])
            action = rules.hotkey_action(False, card_open, self.manual_paused, unwatched)
            if action == "show_unwatched":              # R11: say why, with a way out, instead of nothing
                if self.widget:
                    self.widget.show_unwatched()
                return
            if action == "close_card_then_open_note":
                self.widget.close_card()                # R2
            if action != "ignore":
                self.open_note()
            return
        if self.widget and self.widget.card_open:
            self.close_interval()
            return                      # the card is open: don't capture it into memory
        now = time.time()
        f = macos.front()

        reason = next((k for k, v in self.flags.items() if v), None)
        if reason is None and f and f["app"] in config.LOCK_APPS:
            reason = "locked"
        if reason is None and self.pin:          # a pin wakes us even when idle
            self.pin = False
            self.capture("pin", pinned=True)
            return
        if reason is None and macos.idle_seconds() > config.IDLE:
            reason = "idle"
        if reason:
            self.close_interval()
            if self.paused != reason:
                line(now_hms(), "", f"paused ({reason})")
                self.paused = reason
            self.pending = None
            return
        if self.paused:
            line(now_hms(), "", "resumed")
            self.paused = None
            self.trigger("resume")

        if now - self.last_poll >= config.POLL:
            self.last_poll = now
            if f and self.last_sig:
                if f["pid"] != self.last_sig["pid"]:
                    self.trigger("app_switch")
                elif f["win_id"] != self.last_sig["win_id"]:
                    self.trigger("window_change")
                elif f["window"] != self.last_sig["window"]:
                    self.trigger("tab_change" if f["app"] in config.BROWSERS else "title_change")
            if f and f["app"] in self.ocr_apps and time.monotonic() - self.last_read > 0.7:
                self.start_read(f, clear=False)      # keyboard, notifications and redraws don't click
            if f and self.last_sig and any(f[k] != self.last_sig[k] for k in ("pid", "win_id", "window")):
                self.identify_now(f)                 # a new app, window or tab
            self.last_sig = f or self.last_sig

        if self.pending and now >= self.pending[1]:
            if now - self.last_capture >= config.MIN_GAP:
                why = self.pending[0]
                self.pending = None
                self.capture(why)
        elif now - self.last_capture >= self.current_interval(now):
            if self.q.qsize() < config.MAX_QUEUE:
                self.capture("timer")
            else:
                self.last_capture = now
                self.stats["backlog_skip"] += 1

    def current_interval(self, now, quiet=False):
        """Seconds until the next timer capture. Fast (`every`) while you're active; each
        unchanged frame doubles it, up to a cap (lower for chats, where text can arrive with
        no input from you). Any input since the last capture, or a switch, resets it."""
        if not quiet and min(macos.input_ages().values()) < (now - self.last_capture) + 0.5:
            self.idle_steps = 0
        return min(self.every * 2 ** self.idle_steps, max(self.interval_cap, self.every))

    def maintain(self):
        """Housekeeping the main loop does between captures."""
        now = time.time()
        if self.dirty and now - self.last_save >= self.save_interval:
            with self.lock:
                self.save(force=True)
        self.sample_resources(now)
        if (config.FIND_PROJECTS and self.paused == "idle" and not self.finder_busy
                and now - self.last_finder >= config.FIND_EVERY):
            self.last_finder, self.finder_busy = now, True
            threading.Thread(target=self.finder_pass, daemon=True).start()
        if now - self.last_retention_sweep >= config.RETENTION_SWEEP:
            self.last_retention_sweep = now
            with self.lock:
                if retention.expire_screenshots(self.items):
                    self.save(force=True)

    # ------------------------------------------------------------ capture

    def capture(self, trigger, pinned=False):
        """Screenshot the front window's display and queue it. Returns its metadata."""
        if self.manual_paused or any(self.flags.values()):
            return None
        self.last_capture = time.time()
        f = macos.front()
        if f is None:
            return None
        if not apps.watched(self.watch_apps, f["bundle_id"]):          # R11: before anything is read or grabbed
            self.skipped_place, self.skip_kind = (f["app"], f["window"]), "unwatched"
            self.close_interval()
            return None
        url, tab_title, private = macos.browser_info(f["app"])
        site = re.sub(r"^https?://", "", url or "").split("/")[0] or None
        title = tab_title or f["window"]

        skip = ("password manager" if f["bundle_id"] in config.BUILTIN_SKIP_APPS
                else "an app you never remember" if f["bundle_id"] in config.SKIP_APPS
                else "private window" if private or config.SKIP_TITLES.search(title or "")
                else "sensitive site" if url and config.SKIP_SITES.search(url)
                else None)
        self.skipped_place = (f["app"], f["window"]) if skip else None     # the pill wears a lock here
        self.skip_kind = "private" if skip else None
        if skip:
            self.close_interval()
            self.stats["skipped"] += 1
            line(now_hms(), f["app"], f"skipped: {skip}")
            return None

        display, frame = macos.display_for(f["bounds"])
        size = frame.size
        inputs, pointer = macos.input_ages(), macos.pointer_on(frame)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        data_dir = config.paths().data_dir
        if ts in self.frames or os.path.exists(os.path.join(data_dir, ts + ".jpg")):
            return None                  # two captures in one second
        capture_start_ns = time.monotonic_ns()
        started = time.perf_counter()
        shot = macos.grab(display) or macos.grab_with_screencapture(display)   # in memory: no files
        self.timed("capture", started)
        capture_end_ns = time.monotonic_ns()
        if shot is None:
            say("  ! screenshot failed (Screen Recording permission?)")
            self.screen_ok, self.screen_checked = False, time.time()
            return None
        after = macos.front()
        if after and (after["pid"], after["win_id"]) != (f["pid"], f["win_id"]):
            # you switched while we were capturing: picture and context no longer match
            shot.release()
            self.trigger("app_switch" if after["pid"] != f["pid"] else "window_change")
            return None
        if trigger == "note":
            if after is None or any(after.get(key) != f.get(key) for key in ("app", "bundle_id", "window", "bounds")):
                shot.release()
                self.trigger("window_change")
                return None
            # a tab can switch without changing the containing window's id/title
            if macos.browser_info(f["app"]) != (url, tab_title, private):
                shot.release()
                self.trigger("tab_change")
                return None
        meta = {
            "ts": ts, "iso": datetime.now().isoformat(timespec="seconds"),
            "app": f["app"], "bundle_id": f["bundle_id"], "window": f["window"],
            "url": url, "site": site, "tab_title": tab_title,
            "screen": {"w": int(size.width), "h": int(size.height)}, "display": display,
            "image": ts + ".jpg",       # the thumbnail's name, if this frame ends up kept
            "trigger": trigger, "pinned": pinned, "session": self.session,
            "place": ax.key(f["app"], ax.read(f["pid"], f["app"])),
            "inputs": inputs, "pointer": pointer,
            "window_region": visible_window_region(f["bounds"], macos.display_bounds(display)),
            "capture_start_offset_ns": capture_start_ns - self.origin_ns,
            "capture_end_offset_ns": capture_end_ns - self.origin_ns,
        }
        place = self.trail.chat_place(f["bundle_id"]) if self.trail else None
        if place:
            meta["trail_chat"] = place["name"]       # understand.chat() uses it instead of the OCR'd header
        monitor = self.input_monitor
        if monitor and monitor.state == "recording" and not monitor.paused:
            permitted = monitor.context_provider(monitor.allowed_apps)
            if (permitted and permitted["id"] == input_hooks.context_id(f) and permitted["bounds"] == f["bounds"]
                    and after and input_hooks.context_id(after) == permitted["id"] and after["bounds"] == f["bounds"]):
                meta["input_context_id"] = permitted["id"]
                with self.lock:
                    meta["input_event_ids"] = self.input_store.link_capture(
                        ts, permitted["id"], meta["capture_start_offset_ns"], meta["capture_end_offset_ns"])
                    self.input_store.keep_capture(ts, shot)
        meta_path = os.path.join(data_dir, ts + ".json")    # written only if this frame is kept
        self.frames[ts] = shot
        self.stats["captured"] += 1
        self.q.put((meta_path, meta, trigger, pinned))
        return meta

    # ------------------------------------------------------------ notes (⌃⌥N)

    def open_note(self):
        """Hotkey: capture what's in front RIGHT NOW, then open the note window for it.
        The note attaches to that capture's item once OCR resolves it."""
        if self.manual_paused or any(self.flags.values()):
            return False
        meta = self.capture("note", pinned=True)
        if meta is None:
            line(now_hms(), "", "note unavailable: current context could not be captured")
            return False
        label = meta["app"] + (f": {meta['tab_title'] or meta['window']}"
                               if meta.get("tab_title") or meta.get("window") else "")
        target = ("frame", meta["ts"])
        here = notes.note_target(self._card_here() if self.widget else None, label)
        sig = self.front_sig()
        self.panel.show(here["crumb"], lambda text: self.save_note(target, text, here["where"], sig))
        self.poll_input()
        return True

    def save_note(self, target, text, where=None, sig=None):
        if not text:
            line(now_hms(), "", "note cancelled")
            return
        if self.widget and where:
            self.widget.flash(f"Saved to {where}"[:44])
        at = datetime.now().isoformat(timespec="seconds")
        with self.lock:
            item = notes.record(self.items, self.frame_item, self.notes, target, text, at)
            self.save(force=True)
            if item is None:
                self.waiting.append({"at": at, "text": text, "sig": sig})      # shown on the pill meanwhile
        self.widget_refresh_soon()
        line(at[11:19], (item or {}).get("app", "?"), "note saved" + (" (awaiting context)" if item is None else ""))

    def window_add_note(self, item_id, text):
        """A note typed in the window, on the thing whose page it is (the same record as ⌃⌥N)."""
        at = datetime.now().isoformat(timespec="seconds")
        with self.lock:
            item = notes.record(self.items, self.frame_item, self.notes, ("item", item_id), text, at)
            if item is not None:
                self.save(force=True)
        line(at[11:19], (item or {}).get("app", "?"), "note saved" if item else "note not saved: that thing is gone")

    def window_project(self, action, args):
        """A change to the project tree made in the window (project_actions.py). Returns what the
        window shows and needs for Undo; ValueError says why not, in words."""
        import project_actions
        import projects
        if action in settings_model.ACTIONS:
            return self.window_settings(action, args.get("value"))
        with self.lock:
            reg = projects.load()
            if action in suggestions.ACTIONS:
                args = {**args, "sug": self.sug}
            result = project_actions.do(reg, self.items, action, **args)
            if action in suggestions.ACTIONS:
                import project_finder
                project_finder.record("answer", action=action, sid=args.get("sid"), ids=list(args.get("ids") or []),
                                      name=args.get("name"), via="window")
            projects.save(reg)
            self.save(force=True)
            if action in suggestions.ACTIONS:
                self.save_sug()
                self.sync_pill()
        line(now_hms(), "", result["message"])
        return result

    def window_settings(self, action, value):
        """A change in the Settings window (settings_model.py): saved to the user block of memory.json and
        applied to the running tracker at once. Returns what the window shows and what Undo needs."""
        with self.lock:
            before = store.load_user() or {}
            result = settings_model.apply(before, action, value)
            store.save_user(result["user"])
            self.apply_user(result["user"])
        line(now_hms(), "", result["message"])
        return {"message": result["message"], "go": None, "stay": True, "undo": {"settings": before}}

    def apply_user(self, user):
        """Make a changed user block true now: which apps are watched, which are never remembered,
        how long screenshots stay. (Name and what you work on are only read from the file.)"""
        self.watch_apps[:] = apps.clean(user.get("watch_apps"))
        if self.trail:
            self.trail.set_watch(self.watch_apps)
        config.set_skip_apps(e["id"] for e in apps.clean(user.get("skip_apps")))
        config.SCREENSHOT_DAYS = settings_model.keep_days(user)
        self.last_retention_sweep = 0.0                  # sweep soon, so a shorter keep time shows its effect

    def settings_view(self, section):
        """The Settings page for the window. Reads macOS and the data folder only for the section that needs it."""
        facts = data = None
        if section == "access":
            status = self.widget_status()
            facts = {"screen": status["screen"], "screen_fresh": status["screen"] or status["restart"],
                     "ax": permissions.accessibility_trusted(),
                     "mic": "denied" if status["mic_off"] else "granted", "speech": "granted"}
        elif section == "data":
            import forget
            try:
                data = forget.plan(config.paths().data_dir)
            except ValueError:
                data = None
        return settings_model.view(store.load_user() or {}, section, facts, data, hotkey=dictation.HOTKEY_LABEL)

    def window_project_undo(self, undo):
        import project_actions
        import projects
        if "settings" in undo:
            with self.lock:
                store.save_user(undo["settings"])
                self.apply_user(undo["settings"])
            line(now_hms(), "", "undone")
            return
        with self.lock:
            reg = projects.load()
            project_actions.undo(reg, self.items, undo, self.sug)
            projects.save(reg)
            self.save(force=True)
            if undo.get("sug") is not None:
                self.save_sug()
                self.sync_pill()
        line(now_hms(), "", "undone")

    def widget_refresh_soon(self):
        self.last_widget_refresh = 0.0

    # ------------------------------------------------------------ the on-screen pill

    def widget_status(self):
        """What is wrong right now, for the pill's marks: screen access, a private window, the mic."""
        if time.time() - self.screen_checked > 5:
            self.screen_ok, self.screen_checked = macos.screen_recording_allowed(request=False), time.time()
        kind = "manual" if self.manual_paused else "away" if self.paused == "idle" else None
        if self.screen_ok:
            self.screen_restart = False
        elif time.time() - self.restart_checked > 15:           # on in System Settings, but only for a new run?
            self.restart_checked = time.time()
            threading.Thread(target=self.check_restart, daemon=True).start()   # a child process: never on the UI thread
        now = datetime.now()
        end = datetime.fromtimestamp(self.pause_until) if self.pause_until else None
        return {"screen": self.screen_ok, "restart": self.screen_restart, "mic_off": dictation.mic_off(),
                "private": self.skip_kind == "private" and self.skipped_place == self.front_sig(),
                "unwatched": self.skip_kind == "unwatched" and self.skipped_place == self.front_sig(),
                "paused": kind, "pause_view": rules.paused_view(kind, end, now) if kind else None,
                "suggestions": len(self.sug["projects"]) + len(self.sug["items"])}

    def check_restart(self):
        self.screen_restart = permissions.screen_allowed_fresh()

    def window_view(self, status):
        """The window's banner needs the memory's state too: setup not finished shows as such. (A
        damaged memory file stops LMemM before it gets here, so the window never sees that one.)"""
        memory = window_model.MEMORY_OK if store.load_user() else window_model.MEMORY_FIRST_RUN
        return window_model.view(status, memory, self.watch_apps)

    def page_data(self):
        """What the project page reads: the project tree (a small file) and the remembered things.
        The page only looks; nothing it does writes to either."""
        import projects
        try:
            reg = projects.load()
        except ValueError as error:                  # damaged: show no projects, never replace the file
            if not self.page_data_said:
                self.page_data_said = True
                say(str(error))
            reg = projects.empty()
        return reg, dict(self.items)               # a copy, so the resolver thread can go on writing

    def adopt_projects(self):
        """Give every project name older versions filed things under a place in the project tree
        (projects.adopt; safe to repeat). Writes only when something changed."""
        import projects
        try:
            reg = projects.load()
            with self.lock:
                changed = projects.adopt(reg, self.items)
                if changed:
                    projects.save(reg)
                    self.save(force=True)
            if changed:
                say(f"Projects: placed {changed} thing(s) in the project tree")
        except ValueError as error:
            say(str(error))

    def open_window(self):
        if self.main_window is None:
            self.main_window = main_window.MainWindow(self.menu_pick, self.page_data, self.widget_tick, self.window_add_note,
                                                   self.window_project, self.window_project_undo, self.sug_view,
                                                      self.settings_view, on_memory=self.window_memory_action, memory_view=self.window_memory_view)   # its button sends a menu row id
        self.adopt_projects()                       # things filed by name since the last time
        self.main_window.show(self.window_view(self.widget_status()))

    def _memory_controller(self):
        if self.memory_controller is None:
            from memory_controller import MemoryController
            self.memory_controller=MemoryController(self.start_memory,lambda:self.items,lambda:self.save(force=True))
        return self.memory_controller

    def window_memory_view(self,pid):
        controller=self._memory_controller();controller.select(pid)
        if self.memory_client is None:controller.poll([])
        return controller.view()

    def window_memory_action(self,action,payload):
        if action=='note':                              # only a note touches the shared items; the rest must never wait on the lock
            with self.lock:self._memory_controller().action(action,payload)
        else:self._memory_controller().action(action,payload)

    def show_memory_file(self):
        subprocess.run(["open", "-R", config.paths().items_file], check=False)

    def menu_pick(self, row):
        """A row of the menu-bar item was clicked (menu_model.view lists the ids)."""
        kind = menu_model.pause_kind(row)
        if kind:
            self.widget_pause(kind)                     # the same Pause as the pill's (R9)
        elif row == "resume":
            self.widget_resume()
        elif row == "add_note":
            self.note_request = True                    # the same as ⌃⌥N, paused card and all (R9)
        elif row == "open":
            self.open_window()
        elif row == "settings":
            self.open_window()
            self.main_window.open_settings()
        elif row == "access_ax":
            permissions.MacSystem().open_settings("ax")
        elif row == "show_file":
            self.show_memory_file()
        elif row == "access":
            self.check_access()
        elif row in {"restart", "setup", "delete"}:
            if not self.confirm_leaving(row):
                return
            self.after_exit = row
            self.quit_requested = True
        elif row == "quit":
            self.quit_requested = True
        else:
            return
        self.menu_refreshed = 0.0                       # show the new state at the next tick
        if self.nsapp is not None:
            macos.wake(self.nsapp)                      # act now, not at the next 0.25 s boundary

    def check_access(self):
        """"Check access…": open the pane for what is off, or restart when macOS only needs that,
        or say that all is on."""
        status = self.widget_status()
        action = menu_model.access_action(status)
        if action == "restart":
            if self.confirm_leaving("restart"):
                self.after_exit, self.quit_requested = "restart", True
        elif action in {"screen", "mic"}:
            permissions.MacSystem().open_settings("screen" if action == "screen" else "voice")
        elif self.widget:
            self.widget.flash("All access is on")

    def _open_window(self):
        """The main window when it is on screen: a question about it becomes a sheet on it."""
        win = self.main_window
        return win.window if win is not None and win.visible() else None

    def confirm_leaving(self, row):
        """Ask before a restart, reopening setup or deleting everything. The words come from
        menu_model; nothing happens on Cancel."""
        if row == "delete":
            import forget
            try:
                title, text = menu_model.delete_words(forget.plan(config.paths().data_dir))
            except ValueError as error:
                say(str(error))
                return False
            return self.menubar.ask(title, text, "Delete everything", careful=True, window=self._open_window())
        if row == "setup":
            title, text = menu_model.setup_words()
            return self.menubar.ask(title, text, "Reopen setup", window=self._open_window())
        return True                                      # a restart has nothing to lose

    def widget_pause(self, kind):
        """The pill's Pause row: kind is one of rules.PAUSE_CHOICES."""
        end = rules.pause_end(kind, datetime.now())
        self.set_manual_pause(True, end.timestamp() if end else None)
        line(now_hms(), "", "paused" + (f" until {end:%H:%M}" if end else " until resumed"))
        self.widget_refresh_soon()

    def widget_resume(self):
        self.set_manual_pause(False)
        line(now_hms(), "", "resumed")
        if self.widget:
            self.widget.flash("Remembering again")
        self.widget_refresh_soon()

    def front_sig(self):
        """Where you are right now (app, window), read live so the pill never trails a switch."""
        sig = macos.front() or self.last_sig
        return (sig["app"], sig["window"]) if sig else None

    def widget_card(self):
        """What the pill's card shows: the project of the thing you're on right now. A note you
        just wrote there counts at once (rules.py R4), even before its screen has been read."""
        card = self._card_here()
        here = self.front_sig()
        with self.lock:
            for w in list(self.waiting):
                note = next((n for n in self.notes if n["at"] == w["at"] and n["text"] == w["text"]), None)
                if note is None or note.get("status") != "pending":
                    self.waiting.remove(w)             # attached (or given up): the real item has it now
            waiting = [w for w in self.waiting if w["sig"] == here]   # only where you wrote it
        card_out = card
        return rules.with_pending(card_out, waiting, lambda: notes.blank_card(here[0] if here else "", here[0] if here else ""))

    def _card_here(self):
        with self.lock:
            cur = self.events[-1] if self.events else None
            if self.ax_now and self.ax_now["pid"] == (self.last_sig or {}).get("pid"):
                # named by the app itself (a labelled input, or the page's URL); an item of None
                # means a page you have no notes on, which must not show the one you just left
                if self.ax_now["item"] in self.items:
                    return notes.card(self.items, self.ax_now["item"])
                app = (self.last_sig or {}).get("app") or ""
                return notes.blank_card(app, app)          # a place with no notes: say so
            # identify_now() answers in well under a second; the full capture (settle + OCR,
            # ~2 s) is only the fallback, so the pill never shows the thing you just left.
            front = self.last_sig and (self.last_sig["app"], self.last_sig["window"])
            if self.now and self.now["raw"] == front:
                return notes.card(self.items, self.now["item"]) if self.now["item"] in self.items else None
            if cur and self.cur_place == front:
                return notes.card(self.items, cur["item"])
            app = (self.last_sig or {}).get("app") or ""
            return notes.blank_card(app, app)       # not read yet: say "no notes here", never the last thing's

    # ------------------------------------------------------------ "what am I on?", right now

    def set_now(self, raw, item):
        self.now = {"raw": raw, "item": item, "mono": time.monotonic_ns()}

    def trail_state(self, bundle_id, app):
        """The chat state the event trail read from the app (exact name), or None."""
        place = self.trail.chat_place(bundle_id) if self.trail else None
        if not place:
            return None
        host = (place.get("url") or "").split("/")[0]
        return understand.chat_state(understand.SITES.get(host) or app, place["name"])

    def identify_now(self, f):
        """The pill must follow a switch at once, not after the next capture is OCR'd.
        Most places are known from metadata alone (URL, tab title, app: no pixels). When an
        app only shows where you are on screen (a WhatsApp chat), clicks in it start a quick
        read of the window's top strip instead (see on_click). Either way the answer is only
        used until the real capture lands."""
        if not apps.watched(self.watch_apps, f["bundle_id"]):          # R11: nothing is read from an app you did not choose
            self.ocr_apps.pop(f["app"], None)
            with self.lock:
                self.set_now((f["app"], f["window"]), None)
            self.last_widget_refresh = 0.0
            return
        url, tab_title, _private = macos.browser_info(f["app"])
        meta = {"app": f["app"], "bundle_id": f["bundle_id"], "window": f["window"], "url": url,
                "site": re.sub(r"^https?://", "", url or "").split("/")[0] or None, "tab_title": tab_title,
                "ts": "quick", "image": "quick", "quick": True}
        st = identity.quick_state(meta)
        named = self.trail_state(f["bundle_id"], f["app"]) if st is None else None
        if named is not None:                # the app named the chat: no screen read needed
            self.ocr_apps.pop(f["app"], None)
            with self.lock:
                self.set_now((f["app"], f["window"]), identity.find_item(self.items, named))
        elif st is not None:
            self.ocr_apps.pop(f["app"], None)
            with self.lock:
                self.set_now((f["app"], f["window"]), identity.find_item(self.items, st))
        else:
            self.ocr_apps[f["app"]] = meta
            self.start_read(f)               # read the screen for it right away
        self.last_widget_refresh = 0.0

    def on_click(self, event):
        """A click in an app whose place is only on screen (a chat list): drop the old answer
        now, then read the window's header, twice (the screen may still be redrawing)."""
        front = self.last_sig
        if front and front["app"] in self.ocr_apps:
            self.start_read(front)

    def start_read(self, front, clear=True):
        self.quick_seq += 1
        if clear:                                               # never show the chat you just left
            self.set_now((front["app"], front["window"]), None)
            self.last_widget_refresh = 0.0
            self.trigger("window_change")    # and a full capture soon: the quick read is only a head start
        self.last_read = time.monotonic()
        for delay in ((0.05, 0.3, 0.9) if clear else (0.0,)):
            self.quick_q.put((self.quick_seq, front["app"], delay))

    def quick_worker(self):
        while True:
            job = self.quick_q.get()
            if job is None:
                return
            seq, app, delay = job
            try:
                time.sleep(delay)
                if seq != self.quick_seq:
                    continue                           # a newer click superseded this read
                f = macos.front()
                meta = dict(self.ocr_apps.get(app) or {})
                if not f or f["app"] != app or not meta or not f["bounds"]:
                    continue
                cg = macos.grab_strip(f["bounds"], 240)
                if cg is None:
                    continue
                import Quartz
                res = resolver.resolve_image(meta, cg, Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg), fast=True)
                st = identity.quick_state(meta, res)
                if seq != self.quick_seq:
                    continue
                with self.lock:
                    # the rules for a full capture read the header by its position; on a cropped
                    # strip that can miss, so also look for a chat name we already know
                    found = identity.find_item(self.items, st) if st else self.known_chat_in(res, understand.SITES.get(meta.get("site")) or f["app"])
                    if st is None and found is None:
                        continue
                    self.set_now((f["app"], f["window"]), found)
                self.last_widget_refresh = 0.0
                line(now_hms(), "", f"quick: {st['target'] if st else '(name matched)'} -> {'known' if found else 'no notes yet'}")
            except Exception as e:                     # best effort: the full capture is the fallback
                line(now_hms(), "", f"quick identify failed: {e}")

    def known_chat_in(self, res, app):
        """Id of the chat (of this app) whose name is among the strip's text, or None."""
        for o in res["objects"]:
            found = o.get("text") and identity.find_chat(self.items, app, o["text"], threshold=0.9)
            if found:
                return found
        return None

    def ax_loop(self):
        """Every ~0.1 s: ask the app in front where you are (a millisecond, no pixels). Its
        focused input's label names a chat or channel; a browser's page URL names a page.
        When that names an item you've been on, the pill switches to it at once."""
        last, seen = None, 0
        while self.running:
            try:
                if not self.trail:
                    raise LookupError
                seen = self.trail.wait_change(seen, 1.0)    # event-driven: wake when macOS says you moved (1 s at most)
                time.sleep(0.06)                            # let a burst of changes settle
            except Exception:
                time.sleep(0.1)
            try:
                app = macos.NSWorkspace.sharedWorkspace().frontmostApplication()
                if app is None:
                    continue
                pid, name = app.processIdentifier(), app.localizedName()
                named = self.trail_state(app.bundleIdentifier(), name) if apps.watched(self.watch_apps, app.bundleIdentifier()) else None
                if named is not None:               # the event trail read the chat's exact name from the app
                    if (pid, named["target"]) != last:
                        last = (pid, named["target"])
                        with self.lock:
                            self.ax_now = {"pid": pid, "key": None, "item": identity.find_item(self.items, named)}
                        self.last_widget_refresh = 0.0
                        if self.nsapp is not None:
                            macos.wake(self.nsapp)
                    continue
                sig = ax.read(pid, name)
                key = ax.key(name, sig)
                page = (sig["doc"], sig["title"]) if sig and not key and sig["doc"] and name in config.BROWSERS else None
                if (pid, key, page) == last:
                    continue
                last = (pid, key, page)
                with self.lock:
                    if key:
                        item = identity.find_place(self.items, key, sig["label"])
                        self.ax_now = {"pid": pid, "key": key, "item": item} if item else None
                    elif page:
                        st = identity.page_state(name, *page)      # the same identity a capture would give it
                        self.ax_now = ({"pid": pid, "key": None, "item": identity.find_item(self.items, st)}
                                       if st else None)
                    else:
                        self.ax_now = None
                self.last_widget_refresh = 0.0
                if self.nsapp is not None:
                    macos.wake(self.nsapp)              # tick now, not at the next 0.25 s boundary
            except Exception as e:
                line(now_hms(), "", f"accessibility read failed: {e}")

    def widget_heard(self):
        """The pill no longer shows the words (the note card does), so this is always None."""
        return None                     # the note card shows the waveform and words itself

    def widget_tick(self, ids, done):
        with self.lock:
            found = notes.set_done(self.items, ids, done=done)
            if found:
                self.save(force=True)
        line(now_hms(), "", f"{len(found)} note{'s' * (len(found) != 1)} marked {'done' if done else 'open'}")

    # ------------------------------------------------------------ projects (suggestions, picker)

    @property
    def answers_file(self):
        return os.path.join(config.paths().memory_dir, "project_suggestions.json")

    def load_answers(self):
        """How many suggestions you have answered: after a few, the pill stops spelling it out."""
        try:
            return int(json.loads(Path(self.answers_file).read_text()).get("answered", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            return 0

    def count_answer(self):
        self.suggest_answers += 1
        try:
            os.makedirs(os.path.dirname(self.answers_file), exist_ok=True)
            private_write(self.answers_file, {"answered": self.suggest_answers})
        except OSError:
            pass

    @property
    def sug_file(self):
        return os.path.join(config.paths().memory_dir, "suggestions.json")

    def load_sug(self):
        try:
            doc = json.loads(Path(self.sug_file).read_text())
            if isinstance(doc.get("projects"), list) and isinstance(doc.get("items"), list):
                return {**suggestions.empty(), **doc}
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return suggestions.empty()

    def save_sug(self):
        try:
            os.makedirs(os.path.dirname(self.sug_file), exist_ok=True)
            private_write(self.sug_file, self.sug)
        except OSError:
            pass

    def sync_pill(self):
        """The pill shows the first project proposal waiting; answering it anywhere clears it everywhere (S1)."""
        first = self.sug["projects"][0] if self.sug["projects"] else None
        self.suggestion = {"name": first["name"], "ids": first["ids"], "reason": first["reason"]} if first else None

    def sug_view(self):
        """The proposals as the window draws them. Tidies the list first (S3) and keeps the pill in step."""
        reg, items = self.page_data()
        with self.lock:
            before = repr(self.sug)
            view = suggestions.view(self.sug, reg, items, datetime.now())
            if repr(self.sug) != before:
                self.save_sug()
            self.sync_pill()
        return view

    def finder_pass(self):
        """Look for projects in what you did lately and offer them (project_finder.py). Runs on its own
        thread while you are idle: the method works on a copy, the lock is held only to copy and to offer."""
        import copy
        import project_finder
        import project_reasoner
        import projects
        import real_days
        try:
            reg = projects.load()
            with self.lock:
                if project_finder.shelve(self.sug, datetime.now()):    # unanswered for days: make room
                    self.save_sug()
                    self.sync_pill()
                if len(self.sug["projects"]) + len(self.sug["items"]) >= project_finder.MAX_WAITING:
                    return
                items = copy.deepcopy(self.items)
            visits = real_days.read_visits(config.paths().memory_dir)
            namer = (project_reasoner.OllamaModel(config.NAME_MODEL, config.NAME_MODEL_URL, timeout=30,
                                                  keep_alive="20s")
                     if config.NAME_MODEL else None)
            planned = project_finder.find(reg, items, visits, namer=namer)
            with self.lock:
                added = project_finder.offer(self.sug, reg, self.items, planned)
                if added:
                    self.save_sug()
                    self.sync_pill()
            for e in added:
                line(now_hms(), "", f"suggesting {e.get('name') or projects.get(reg, e['pid'])['name']} "
                                    f"({len(e['ids'])} things): {e['reason']}")
        except Exception as error:             # a look that fails must never stop capture
            line(now_hms(), "", f"project look skipped: {error}")
        finally:
            self.finder_busy = False

    def offer_project(self, name, ids, reason=""):
        """Offer a group of things as one project. The model will call this; until it
        exists, `lmemm.py suggest [NAME] [ID…]` does. With no ids, the four latest things."""
        with self.lock:
            if not ids:
                ids = [i["id"] for i in sorted(self.items.values(), key=lambda i: i["last_seen"], reverse=True)[:4]]
            entry = suggestions.offer_project(self.sug, self.items, name, ids, reason)
            if entry is None:
                line(now_hms(), "", "no project suggestion: needs two known things you have not refused")
                return
            self.save_sug()
            self.sync_pill()
        line(now_hms(), "", f"suggesting project {entry['name']} ({len(entry['ids'])} things)")

    def offer_items(self, project, ids, reason=""):
        """Offer things for an existing project (`lmemm.py suggest-for PROJECT [ID…]`). With no ids, the
        three latest things that are not in it. `project` is a name or a path, as the CLI takes it."""
        import projects
        try:
            reg = projects.load()
            pid = projects.resolve(reg, project)
            with self.lock:
                if not ids:
                    latest = sorted(self.items.values(), key=lambda i: i["last_seen"], reverse=True)
                    ids = [i["id"] for i in latest if pid not in projects._members_of(i)][:3]
                entry = suggestions.offer_items(self.sug, reg, self.items, pid, ids, reason)
                if entry is not None:
                    self.save_sug()
        except ValueError as error:
            say(str(error))
            return
        line(now_hms(), "", f"suggesting {len(entry['ids'])} things for {projects.get(reg, pid)['name']}" if entry
             else "no suggestion: nothing there that is not already in it or refused")

    def in_tree(self, change):
        """Run change(reg) on the project tree and save it, so what the pill does and what the window
        shows agree (thing_actions T6). Called under the memory lock. Returns what change returned,
        or None when the tree could not be read."""
        import projects
        try:
            reg = projects.load()
            result = change(reg)
            projects.save(reg)
            return result
        except ValueError as error:
            say(str(error))
            return None

    def widget_suggestion(self):
        with self.lock:
            return notes.suggestion_view(self.suggestion, self.items, self.suggest_answers)

    def widget_project(self, ids, name):
        """Put things in a project: by accepting a suggestion, or from the picker."""
        with self.lock:
            found = notes.set_project(self.items, ids, name)
            found = self.in_tree(lambda reg: thing_actions.file_by_name(reg, self.items, ids, name)) or found
            if found:
                self.save(force=True)
            if self.suggestion and set(self.suggestion["ids"]) & set(found):
                self.count_answer()
                import project_finder
                project_finder.record("answer", action="accept_project", ids=list(found), name=name, via="pill")
            suggestions.drop_ids(self.sug, found)
            self.save_sug()
            self.sync_pill()
        line(now_hms(), "", f"{len(found)} thing{'s' * (len(found) != 1)} filed in {name}")

    def widget_decline(self, forever=True):
        with self.lock:
            if self.suggestion and forever:
                import project_finder
                project_finder.record("answer", action="dismiss_project", ids=list(self.suggestion["ids"]),
                                      name=self.suggestion["name"], via="pill")
                notes.decline_project(self.items, self.suggestion["ids"], self.suggestion["name"])
                self.in_tree(lambda reg: thing_actions.decline_by_name(reg, self.items, self.suggestion["ids"], self.suggestion["name"]))
                self.save(force=True)
                self.sug["projects"] = [e for e in self.sug["projects"] if e["ids"] != self.suggestion["ids"]]
                self.save_sug()
            self.sync_pill()
            self.count_answer()

    def widget_picker(self, item_id, query):
        with self.lock:
            return notes.picker_view(self.items, item_id, query)

    def widget_unfile(self, item_id):
        with self.lock:
            found = self.in_tree(lambda reg: thing_actions.unfile_main(reg, self.items, [item_id])) \
                or notes.clear_project(self.items, [item_id])
            if found:
                self.save(force=True)

    # ------------------------------------------------------------ resolver thread

    def worker(self):
        while True:
            job = self.q.get()
            if job is None:
                return
            meta_path, meta, trigger, pinned = job
            try:
                with objc.autorelease_pool():
                    self.handle(meta_path, meta, trigger, pinned)
            except Exception as e:
                say(f"  ! {meta['ts']}: {e}")
                with self.lock:
                    notes.fail_pending(self.notes, meta["ts"])
                    self.frame_item[meta["ts"]] = None
                    if self.running and not (self.input_store and self.input_store.closed):
                        self.save()
            finally:
                self.q.task_done()

    def handle(self, meta_path, meta, trigger, pinned):
        started = time.perf_counter()
        now = time.mktime(time.strptime(meta["ts"], "%Y%m%d-%H%M%S"))
        frame = self.frames.pop(meta["ts"], None)         # in memory (normal), or None: read the file
        img = frame.gray if frame is not None else \
            activity.load(os.path.join(config.paths().data_dir, meta["image"]))
        last = self.last_frame
        sig = (meta.get("app"), meta.get("window"), meta.get("url"))
        try:
            # pixels first: an unchanged screen of the same window needs no OCR at all
            change = activity.diff(last["img"], img) if last is not None and last["sig"] == sig else None
            if change is not None and not change["regions"] and not pinned:
                res, st = last["res"], last["st"]
                self.stats["no_ocr"] += 1
                self.idle_steps = min(self.idle_steps + 1, 8)     # nothing changed: slow the timer
            else:
                self.idle_steps = 0
                res = self.read(meta_path, meta, frame, last, change, pinned)
                st = understand.describe(res, meta)
            self.interval_cap = (config.BACKOFF_CAP_CHAT if st["kind"] in CHATTY else config.BACKOFF_CAP)
            content = extract_content(res, meta)

            # OCR ran outside the memory lock so note saves never wait for Vision
            with self.lock:
                self.remember(meta, trigger, pinned, now, img, last, change, res, st, content, frame)
        finally:
            if frame is not None:
                frame.release()
            self.timed("handle", started)

    def read(self, meta_path, meta, frame, last, change, pinned):
        """OCR a changed screen. Fast OCR (~9x cheaper) for a later frame of the same window;
        accurate OCR for the first look at it, and whenever the fast pass looks thin."""
        if frame is None:
            return resolver.resolve(meta_path)             # a file on disk: always accurate
        continuation = (config.FAST_CONTINUATION and last is not None and change is not None
                        and not pinned and meta.get("trigger") not in ("note", "pin"))
        started = time.perf_counter()
        res = resolver.resolve_frame(meta, frame, fast=continuation)
        if continuation and self.looks_thin(res, last["res"]):
            self.timed("ocr_fast", started)
            started = time.perf_counter()
            res = resolver.resolve_frame(meta, frame, fast=False)
            self.timed("ocr_redo", started)
            self.timed("ocr_accurate", started)
            return res
        self.timed("ocr_fast" if continuation else "ocr_accurate", started)
        return res

    @staticmethod
    def looks_thin(res, previous):
        """Did the fast pass find far fewer lines than the last frame of this window?"""
        count = lambda r: sum(1 for o in r["objects"] if o.get("text"))
        before = count(previous)
        return before >= 12 and count(res) < config.THIN_RATIO * before

    def remember(self, meta, trigger, pinned, now, img, last, change, res, st, content, frame=None):
        """A resolved screen -> which thing, what you did on it, memory + timeline."""
        if not self.running or self.input_store and self.input_store.closed:
            return
        sig = (meta.get("app"), meta.get("window"), meta.get("url"))

        # 1. which thing is this?
        cur = self.events[-1] if self.events and not self.events[-1].get("_closed") else None
        scrolled = bool(change and change["scroll"]) or (meta.get("inputs") or {}).get("scroll", 1e9) < 3 * self.every
        item_id, ref = identity.resolve_item(self.items, st, cur, res, trigger, scrolled)
        item = self.items.get(item_id)
        same_thing = last is not None and last["item"] == item_id
        if not same_thing:
            change = None                  # diffing two different things means nothing
        elif change is None:
            change = activity.diff(last["img"], img)    # same thing, its URL/title changed

        # 2. what happened since the last frame of it: typing / reading / receiving / focus
        dt = min(now - last["t"], config.BACKOFF_CAP + self.every) if same_thing else 0
        scale = res["image_size"]["w"] / meta["screen"]["w"] if meta.get("screen") else 1
        ptr = meta.get("pointer")
        act = activity.classify(change, last["res"] if same_thing else None, res,
                                meta.get("inputs") or {"key": 1e9, "scroll": 1e9, "click": 1e9},
                                (ptr["x"] * scale, ptr["y"] * scale) if ptr else None, dt,
                                kind=st["kind"])
        self.last_frame = {"sig": sig, "img": img, "res": res, "st": st, "item": item_id, "t": now}
        if act["category"] == "typing":
            st = dict(st, doing=st.get("doing_typing") or st["doing"])

        # 3. the memory entry for it
        item, frame_used = self.update_item(item, item_id, ref, st, meta, content, pinned)
        identity.learn_place(item, meta.get("place"))
        self.record_activity(item, act, dt, res)

        # 4. the timeline
        t = hms(meta["ts"])
        if cur and cur["item"] == item_id:
            self.extend(cur, now, t)
            cur["doing"] = st["doing"]
        else:
            if cur:
                self.close_event(cur, last, now, t)
            # Did you come BACK to it? Not if the previous stretch was this same thing and
            # only got split by the note window, the pill's card, a pause or a lock.
            prev = self.events[-1] if self.events else None
            came_back = prev is None or prev["item"] != item_id \
                or now - (prev["_start"] + prev["seconds"]) > config.RESURFACE_COOLDOWN
            item["visits"] += 1
            cur = {"from": t, "to": t, "seconds": 0, "item": item_id, "app": st["app"],
                   "doing": st["doing"], "activity": {}, "trigger": trigger, "_start": now}
            self.events.append(cur)
            line(t, st["app"], st["doing"] + ("  (back to it)" if came_back and item["visits"] > 1 else ""))
            if came_back:
                self.resurface(item, cur, meta)
        self.cur_place = (meta["app"], meta["window"])      # where the newest event was read
        if dt:
            cur["activity"][act["category"]] = cur["activity"].get(act["category"], 0) + int(dt)
            cur["mostly"] = max(cur["activity"], key=cur["activity"].get)
        cur["_last_cat"] = act["category"]
        cur["_raw"] = (meta.get("app"), meta.get("window"))
        if (self.now and self.now["raw"] == cur["_raw"]
                and meta["capture_start_offset_ns"] + self.origin_ns > self.now["mono"]):
            self.now = None                  # a capture taken after the quick answer: it is the truth
        if act["new_text"] and act["category"] in ("typing", "receiving"):
            line(t, "", f"  {act['category']}: {act['new_text'][0][:80]}")

        # 5. bookkeeping
        self.frame_item[meta["ts"]] = item_id
        if self.input_store:
            self.input_store.observe(item_id, content, meta, pinned, ref)
            if meta.get("input_context_id"):
                self.input_store.link_capture(meta["ts"], meta["input_context_id"],
                                              meta["capture_start_offset_ns"], meta["capture_end_offset_ns"], item_id)
        notes.attach_pending(self.items, self.frame_item, self.notes)
        if frame_used:
            self.keep_thumbnail(meta, frame)
        else:
            self.drop_frame(meta["image"])               # (only exists when read from a file)
            self.stats["no_change"] += 1
        self.save()

    def keep_thumbnail(self, meta, frame):
        """A frame that became a thing's screenshot: write it as a small thumbnail, plus its
        capture metadata. (A frame read from a file is already on disk, as it was.)"""
        if frame is None:
            return
        data_dir = config.paths().data_dir
        os.makedirs(data_dir, exist_ok=True)
        path = os.path.join(data_dir, meta["image"])
        frame.save(path, long_edge=config.THUMB_PX, quality=config.THUMB_QUALITY)
        meta["bytes"] = os.path.getsize(path)
        with open(os.path.join(data_dir, meta["ts"] + ".json"), "w") as fh:
            json.dump(meta, fh, indent=2)

    def resurface(self, item, event, meta):
        """Back on something with open notes: remind you of them (at most once per
        RESURFACE_COOLDOWN per thing)."""
        due = notes.due_for_resurfacing(item, meta["iso"], config.RESURFACE_COOLDOWN)
        if not due:
            return
        item["resurfaced_at"] = meta["iso"]
        event["resurfaced"] = [notes.note_id(n) for n in due]
        what = item.get("title") or item["doing"]
        count = f"{len(due)} pending edit{'s' * (len(due) != 1)}"
        line(hms(meta["ts"]), "", f"  📝 {count}: " + " · ".join(n["text"][:60] for n in due[:3]))
        macos.notify(f"LMemM · {count}", f"{what[:60]} — " + " · ".join(n["text"] for n in due[:3]))

    def update_item(self, item, item_id, ref, st, meta, content, pinned):
        """Create the entry, or update it in place (one screenshot per thing: the latest
        one that showed something new). Returns (item, whether this frame was kept)."""
        state_hash = hashlib.sha1(json.dumps(st["details"], sort_keys=True).encode()).hexdigest()[:12]
        frame_used = False
        if item is None:
            item = self.items[item_id] = {
                "id": item_id, "app": st["app"], "kind": st["kind"], "title": st["title"],
                "doing": st["doing"], "mostly": None, "state": st["details"],
                "activity": {c: {"seconds": 0, "text": []} for c in store.CATEGORIES},
                "first_seen": meta["iso"], "last_seen": meta["iso"],
                "seconds": 0, "visits": 0, "updates": 0,
                "screenshot": meta["image"], "content_hash": state_hash, "ref": ref, "refs": [ref]}
            frame_used = True
            self.stats["new_items"] += 1
            remember_content(item, content)
        else:
            item["last_seen"] = meta["iso"]
            item["doing"] = st["doing"]
            new_content = remember_content(item, content)
            if state_hash != item["content_hash"] or new_content or pinned:
                item.update(state=st["details"] or item["state"], title=st["title"] or item["title"],
                            content_hash=state_hash)
                item["updates"] += 1
                self.drop_frame(item["screenshot"])
                item["screenshot"] = meta["image"]
                frame_used = True
        if pinned:
            item["pinned"] = True
        return item, frame_used

    @staticmethod
    def record_activity(item, act, dt, res):
        """Add this moment to the thing's activity: seconds, and the text involved."""
        a = item["activity"][act["category"]]
        a["seconds"] += int(dt)
        if act["category"] == "receiving" and act["new_text"]:
            a["count"] = a.get("count", 0) + 1
        texts = act["new_text"] + ([act["pointer_on"]] if act.get("pointer_on") and act["category"] == "focus" else [])
        for txt in texts:
            if txt in a["text"]:
                a["text"].remove(txt)
            a["text"].append(txt)
        del a["text"][:-config.KEEP_TEXT]
        if act["category"] == "typing":       # remember where you type in it
            boxes = [o["box"] for o in res["objects"] if o["text"] in act["new_text"]]
            if item.get("typing_area"):
                boxes.append(item["typing_area"])
            if boxes:
                x0 = min(b[0] for b in boxes); y0 = min(b[1] for b in boxes)
                x1 = max(b[0] + b[2] for b in boxes); y1 = max(b[1] + b[3] for b in boxes)
                item["typing_area"] = [x0, y0, x1 - x0, y1 - y0]
        item["mostly"] = store.mostly(item["activity"])
        if not item["title"] and identity.authored(item):
            item["title"] = item["activity"]["typing"]["text"][0][:60]   # untitled: name it by what you wrote

    def close_event(self, cur, last, now, t):
        """Leaving a thing: the gap since its last frame counts as more of the same."""
        gap = int(now - (last["t"] if last else now))
        cat = cur.get("_last_cat") or "reading"
        if gap > 0 and cur["item"] in self.items:
            cur["activity"][cat] = cur["activity"].get(cat, 0) + gap
            cur["mostly"] = max(cur["activity"], key=cur["activity"].get)
            prev = self.items[cur["item"]]
            prev["activity"][cat]["seconds"] += gap
            prev["mostly"] = store.mostly(prev["activity"])
        self.extend(cur, now, t)

    def extend(self, ev, now, t):
        secs = int(now - ev["_start"])
        if ev["item"] in self.items:                 # a thing you forgot from the window is no longer there to add time to
            self.items[ev["item"]]["seconds"] += secs - ev["seconds"]
        ev["seconds"], ev["to"] = secs, t

    def drop_frame(self, image):
        if not image:
            return
        data_dir = config.paths().data_dir
        for p in (os.path.join(data_dir, image), os.path.join(data_dir, image[:-4] + ".json")):
            try:
                os.remove(p)
            except OSError:
                pass

    def save(self, force=False):
        """Write memory to disk. Called under the memory lock. While running, writes are
        batched to one per `save_interval` (the main loop flushes); force=True writes now
        (notes, ticking an edit, shutdown)."""
        if not force and self.save_interval and time.time() - self.last_save < self.save_interval:
            self.dirty = True
            return
        started = time.perf_counter()
        if self.input_store:
            self.input_store.checkpoint(self.items, self.events, self.notes)
        store.save_memory(self.items)
        store.save_session(self.session, self.events, self.notes)
        self.last_save, self.dirty = time.time(), False
        self.timed("save", started)

    # ------------------------------------------------------------ lifecycle

    def run(self):
        p = config.paths()
        set_up = onboarding.load_state(p.onboarding_file)["completed"]
        allowed = macos.screen_recording_allowed(request=not set_up)      # setup has already asked, calmly
        if not allowed and not set_up:
            sys.exit("Screen Recording permission is not granted to this terminal.\n"
                     "System Settings -> Privacy & Security -> Screen Recording, enable it,\n"
                     "quit and reopen the terminal, then run again.")
        if not allowed:                          # you finished setup without it: keep running, the pill says so
            self.screen_ok, self.screen_checked = False, time.time()
            say("Screen Recording is off, so LMemM can't see your screen yet. The pill shows a red mark;\n"
                "turn it on in System Settings -> Privacy & Security -> Screen Recording, then restart LMemM.")
        os.makedirs(p.memory_dir, exist_ok=True)
        with open(p.pidfile, "w") as fh:
            fh.write(str(os.getpid()))
        self.save_interval = config.SAVE_EVERY          # tests save immediately; the real loop batches
        with self.lock:
            stale = os.path.exists(os.path.join(p.memory_dir, ".index.json"))
            expired = retention.expire_screenshots(self.items)
            if stale or expired:                        # one-time file consolidation, or just housekeeping
                self.save(force=True)
        self.last_retention_sweep = time.time()

        def stop(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGUSR1, lambda *_: setattr(self, "pin", True))
        signal.signal(signal.SIGUSR2, lambda *_: setattr(self, "note_request", True))
        app = dictation.start_app()
        self.nsapp = app
        if (Path(p.semantic_dir) / "memory.sqlite").exists():
            self.start_memory()
        hotkey_ok = dictation.register_hotkey(lambda: setattr(self, "note_request", True))
        dictation.ensure_listener()        # build the speech helper now, not on first ⌃⌥N
        if self.show_widget:
            self.widget = widget.Widget(self.widget_card, self.widget_tick,
                                        on_add=lambda: setattr(self, "note_request", True),
                                        heard=self.widget_heard, hotkey=dictation.HOTKEY_LABEL,
                                        suggestion=self.widget_suggestion, on_project=self.widget_project,
                                        on_decline=self.widget_decline, picker=self.widget_picker,
                                        on_unfile=self.widget_unfile, busy=lambda: self.panel.open, status=self.widget_status,
                                        on_pause=self.widget_pause, on_resume=self.widget_resume)
            try:
                self.menubar = menubar.MenuBar(self.menu_pick)
                say(f"Menu-bar item: {self.menubar.where()}")
            except Exception as e:                      # the pill still works; say why the menu is missing
                self.menubar = None
                say(f"Menu-bar item could not be made: {e!r}")
        if self.input_monitor:
            self.input_monitor.start(request_permission=True)
            say(f"Input monitoring: {self.input_monitor.status()['state']} · allowed app: VS Code · no key values recorded")
            if self.input_monitor.state == "unavailable":
                say("Enable Input Monitoring and Accessibility for the launching app in System Settings, then restart; capture still works.")
        self.publish_status()

        self._events = macos.subscribe(self)
        self.thread = threading.Thread(target=self.worker, daemon=True)
        self.thread.start()
        threading.Thread(target=self.quick_worker, daemon=True).start()
        if self.use_trail:
            try:
                import trail_mac
                self.trail = trail_mac.Trail(watch_apps=self.watch_apps,
                                             paused=lambda: self.manual_paused or self.paused is not None)
                self.trail.start()
            except Exception as e:                       # the screenshot tracker must run even if the trail cannot
                self.trail = None
                say(f"event trail off ({type(e).__name__}: {e})")
        threading.Thread(target=self.ax_loop, daemon=True).start()
        self.click_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(1 << 1, self.on_click)

        macos.quiet_system_logs()
        say(f"LMemM is watching  ·  captures on app/tab switches, else every {self.every}s"
            f"  ·  pauses after {config.IDLE}s idle")
        say((f"{dictation.HOTKEY_LABEL} dictate a note" if hotkey_ok
             else f"(couldn't register {dictation.HOTKEY_LABEL}: use  python3 lmemm.py note)")
            + "  ·  Ctrl-C stop\n")
        self.last_sig = macos.front()
        if self.last_sig:
            self.identify_now(self.last_sig)     # so the app you start in is followed too
        self.last_capture = time.time()      # the "start" trigger takes the first frame
        self.trigger("start")
        try:
            while True:
                macos.next_event(app)
                self.tick()
        except KeyboardInterrupt:
            pass
        finally:
            self.finish()
        self.hand_over()

    def hand_over(self):
        """Restart, reopen setup, or delete everything, once LMemM has saved and stopped. A restart
        uses a fresh process: replacing a launched AppKit process in place can leave
        its status item unregistered with macOS even though floating panels work."""
        if not self.after_exit:
            return
        from semantic_memory.runtime_environment import launch_argv
        if self.after_exit == "delete":
            import forget
            try:
                result = forget.delete_all(config.paths().data_dir, None)
                say(f"deleted {result['files']} file(s). LMemM will ask you to set up again.")
            except ValueError as error:
                say(str(error))
            argv = launch_argv(["start"])
        elif self.after_exit == "setup":
            argv = launch_argv(["setup", "--again"])
        else:
            argv = launch_argv(sys.argv[1:])
        subprocess.Popen(argv)

    def start_memory(self):
        if self.memory_client is None:
            from memory_client import MemoryClient
            self.memory_client = MemoryClient(config.paths().semantic_dir)
            self.memory_client.start()
        return self.memory_client

    def finish(self):
        p = config.paths()
        if self.memory_client:
            self.memory_client.stop()
        if self.input_monitor:
            self.poll_input()
            self.input_monitor.stop()
        if self.panel.open:
            self.panel.close(save=False)
        if self.main_window:
            self.main_window.window.orderOut_(None)
        if self.menubar:
            self.menubar.remove()
        say("\nstopping…")
        if self.trail:
            self.trail.stop()
        self.q.put(None)
        self.thread.join(timeout=60)
        with self.lock:
            if self.dirty:
                self.save(force=True)                 # flush anything batched
            self.running = False
        if self.input_store:
            with self.lock:
                self.input_store.set_status({"state": "off"})
                self.input_store.close()
        self.status_file.unlink(missing_ok=True)
        try:
            if open(p.pidfile).read().strip() == str(os.getpid()):
                os.remove(p.pidfile)
        except OSError:
            pass
        self.print_summary()

    def print_summary(self):
        s = self.stats
        spent, visits = Counter(), Counter()
        for e in self.events:                       # this session only
            spent[e["item"]] += e["seconds"]
            visits[e["item"]] += 1
        per_item = Counter(n["item"] for n in self.notes)
        first = self.events[0]["from"] if self.events else "-"
        last = self.events[-1]["to"] if self.events else "-"
        plural = lambda n, w: f"{n} {w}{'s' * (n != 1)}"
        say(f"\nsession {first} → {last}  ·  {s['captured']} screenshots → {plural(len(spent), 'thing')}"
            + (f"  ·  {plural(len(self.notes), 'note')}" if self.notes else "")
            + (f"  ·  {s['skipped']} skipped (sensitive)" if s["skipped"] else "") + "\n")
        for iid, secs in spent.most_common():
            if iid not in self.items:                    # forgotten from the window during the session
                continue
            i = self.items[iid]
            extra = "  ·  ".join(x for x in [f"{visits[iid]} visits" if visits[iid] > 1 else "",
                                             i.get("mostly") or "",
                                             plural(per_item[iid], "note") if per_item[iid] else ""] if x)
            say(f"  {store.duration(secs):>7}  {i['app'][:14]:14}  {i['doing'][:70]}"
                + (f"   ({extra})" if extra else ""))
        self.sample_resources(time.time() + 30)          # a final reading
        c = self.cost_report()
        ms = c["avg_ms"]
        say(f"\ncost: {c['running_for']} running, {c['cpu_pct_average']}% of a core on average, "
            f"memory {c['memory_mb']} MB (peak {c['peak_memory_mb']} MB)")
        say(f"      {c['captures']} captures: {c['skipped_ocr_unchanged']} unchanged (no OCR), "
            f"{c['ocr']['fast']} fast OCR, {c['ocr']['accurate']} accurate OCR; "
            f"avg capture {ms['capture']} ms, fast OCR {ms['ocr_fast']} ms, accurate OCR {ms['ocr_accurate']} ms")
        say(f"\nsaved to {os.path.relpath(config.paths().items_file)}")


# ---------------------------------------------------------------- talking to a running tracker

def running_pid():
    """PID of the running tracker, or None."""
    try:
        pid = int(Path(config.paths().pidfile).read_text().strip())
        if pid <= 0:
            return None
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def send_control(action, **fields):
    """Ask the running tracker to do something; it applies it on its next tick."""
    pid = running_pid()
    if pid is None:
        return False
    private_write(config.paths().control_file, {"pid": pid, "action": action, **fields})
    return True


def signal_running(sig):
    pid = running_pid()
    if pid is None:
        sys.exit("LMemM isn't running.")
    os.kill(pid, sig)


def pin():
    signal_running(signal.SIGUSR1)
    print("pinned the current screen.")


def note():
    signal_running(signal.SIGUSR2)
    print("note window opened.")


if __name__ == "__main__":
    Tracker().run()
