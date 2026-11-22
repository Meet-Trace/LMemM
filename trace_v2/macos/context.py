"""ContextSource: what is in front, from metadata only (never pixels).

context() -> trace_v2.observations.Context
    application  {bundle_id, name, pid}
    window       {id, title, bounds [x,y,w,h]}  + url / private (browsers, see browser.py) + url_error /
                 private_error codes. Exactly {"locked": True} when the screen is locked (nothing else is read).
                 {} when the app has no on-screen window.
    display      {id, bounds, pixel_size, scale} of the display holding most of the window (main display if none)
"""

from trace_v2.observations import Context

from . import browser, displays, session, windows


class ContextSource:
    def __init__(self, browser_state=browser.browser_state):
        self._browser_state = browser_state

    def context(self):
        app = windows.frontmost_application()
        if session.screen_locked(app["bundle_id"]):
            # Nothing about the window behind the lock screen is read: no title, bounds or URL.
            return Context(app, {"locked": True}, displays.display_info(displays.main_display()))
        window = windows.front_window(app["pid"]) or {}
        if window:
            window.update(self._browser_state(app["bundle_id"]))
        display_id = displays.display_for_rect(window["bounds"]) if "bounds" in window else displays.main_display()
        return Context(app, window, displays.display_info(display_id))

    def secure_input(self):
        return session.secure_input()
