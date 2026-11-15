"""System notifications for the runner: sleep/wake, fast user switching, screen lock, front-app changes, plus the
run-loop pump.

Everything is delivered on the thread that pumps (the runner's main thread), through `notify(name, value=None)` with
one of sleep, wake, session_resign, session_active, screen_locked, screen_unlocked, or app_activated (value = the new
front application's bundle id, None when it has none). Observers are removed by `close()`. A failing `notify` is logged
and never propagates into the run loop."""

import logging

import AppKit
import Foundation
import Quartz

log = logging.getLogger("trace_v2.runner")

WORKSPACE = {AppKit.NSWorkspaceWillSleepNotification: "sleep", AppKit.NSWorkspaceDidWakeNotification: "wake",
             AppKit.NSWorkspaceSessionDidResignActiveNotification: "session_resign",
             AppKit.NSWorkspaceSessionDidBecomeActiveNotification: "session_active"}
DISTRIBUTED = {"com.apple.screenIsLocked": "screen_locked", "com.apple.screenIsUnlocked": "screen_unlocked"}


def activated_bundle_id(note):
    """The bundle id of the application an NSWorkspaceDidActivateApplicationNotification is about, or None."""
    try:
        app = note.userInfo()[AppKit.NSWorkspaceApplicationKey]
        return None if app is None else app.bundleIdentifier()
    except (KeyError, TypeError, AttributeError):
        return None


class Notifications:
    def __init__(self, notify):
        self._observers = []
        self._notify = notify
        workspace = AppKit.NSWorkspace.sharedWorkspace().notificationCenter()
        distributed = Foundation.NSDistributedNotificationCenter.defaultCenter()
        for center, table in ((workspace, WORKSPACE), (distributed, DISTRIBUTED)):
            for notification, name in table.items():
                self._observe(center, notification, lambda _note, name=name: self._deliver(name))
        self._observe(workspace, AppKit.NSWorkspaceDidActivateApplicationNotification,
                      lambda note: self._deliver("app_activated", activated_bundle_id(note)))

    def _observe(self, center, notification, handler):
        token = center.addObserverForName_object_queue_usingBlock_(notification, None, None, handler)
        self._observers.append((center, token))

    def _deliver(self, name, value=None):
        try:
            self._notify(name, value)
        except Exception:                        # noqa: BLE001 - never let a handler break the run loop
            log.exception("lifecycle notification %s failed", name)

    def pump(self, seconds):
        """Run the main run loop for up to `seconds` so notifications are delivered."""
        Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, seconds, False)

    def close(self):
        for center, token in self._observers:
            center.removeObserver_(token)
        self._observers = []
