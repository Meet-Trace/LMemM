"""InputGate: whether the input tap may hand an event to the buffer right now.

The capture-level privacy gate (observations.privacy.gate) judges the context once per capture, so on its own it
leaves a gap: the user can switch to a password manager and back between two captures. The tap therefore also
consults this gate, per event, on its own thread. Everything it reads is a plain flag (no I/O, no lock), written by:

    capture_decision(withheld, bundle_id)   the full gate's verdict for the app in front at the latest capture
    app_activated(bundle_id)                NSWorkspace didActivateApplication: the front app changed *now*
    screen_locked(bool)                     com.apple.screenIsLocked / screenIsUnlocked notifications

A blocked gate drops EVERY event (keys, clicks, scrolls and cursor positions alike): click positions inside a password
manager are as sensitive as its keystrokes. The tap counts the drops (`dropped_gated`), never stores them.

A lost notification cannot leave the gate wrong for long: the runner re-reads the front app every few seconds and
feeds it to app_activated() too (reconcile). Fail closed: the gate starts blocked and opens only when the first decision or activation says the front app is fine,
and an activation without a bundle id blocks.
"""

LOCK_SCREEN_BUNDLES = frozenset({"com.apple.loginwindow", "com.apple.ScreenSaver.Engine"})


class InputGate:
    def __init__(self, excluded_apps=frozenset(), start_blocked=True):
        self._excluded = frozenset(excluded_apps) | LOCK_SCREEN_BUNDLES
        self._capture_blocked = start_blocked   # unknown until the first decision or activation
        self._capture_bundle = None
        self._app_blocked = False
        self._current_bundle = None
        self._locked = False
        self._forced = False

    @property
    def blocked(self):
        return self._forced or self._locked or self._app_blocked or self._capture_blocked

    def reason(self):
        """Why events are being dropped now (for stats and tests), or None."""
        for name, on in (("forced", self._forced), ("screen_locked", self._locked), ("app_activated", self._app_blocked),
                         ("capture_gate", self._capture_blocked)):
            if on:
                return name
        return None

    def set_excluded(self, apps):
        self._excluded = frozenset(apps) | LOCK_SCREEN_BUNDLES
        self._app_blocked = self._is_blocked_app(self._current_bundle) if self._current_bundle is not None else self._app_blocked

    def _is_blocked_app(self, bundle_id):
        return bundle_id is None or bundle_id in self._excluded

    def capture_decision(self, withheld, bundle_id):
        """The full gate's verdict (including private windows, sites, secure input) for the latest capture. It only
        ever sets the capture-level flag: the app-level flag belongs to activations, so a capture that read its context
        a moment before the user switched apps can never undo a block that the switch just made."""
        self._capture_bundle = bundle_id
        self._capture_blocked = bool(withheld)

    def app_activated(self, bundle_id):
        self._current_bundle = bundle_id
        self._app_blocked = self._is_blocked_app(bundle_id)
        if bundle_id != self._capture_bundle:
            self._capture_blocked = False        # that verdict was about a different app; the next capture decides

    def screen_locked(self, locked):
        self._locked = bool(locked)

    def force(self, blocked):
        """Manual override (the tap's `keyboard_blocked` compatibility setter)."""
        self._forced = bool(blocked)
