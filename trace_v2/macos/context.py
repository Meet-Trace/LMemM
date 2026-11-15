"""ContextSource: what is in front, from metadata only (never pixels).

context() -> trace_v2.observations.Context
    application  {bundle_id, name, pid}
    window       {id, title, bounds [x,y,w,h]}  + url / private (browsers, see browser.py) + url_error /
                 private_error codes + locked=True when the screen is locked. {} when the app has no on-screen window.
    display      {id, bounds, pixel_size, scale} of the display holding most of the window (main display if none)
"""

from trace_v2.observations import Context

from . import browser, displays, session, windows


class ContextSource:
    def __init__(self, browser_state=browser.browser_state):
        self._browser_state = browser_state

    def context(self):
        app = windows.frontmost_application()
        window = windows.front_window(app["pid"]) or {}
        locked = session.screen_locked(app["bundle_id"])
        if locked:
            window["locked"] = True
        elif window:
            window.update(self._browser_state(app["bundle_id"]))
        display_id = displays.display_for_rect(window["bounds"]) if window else displays.display_for_rect([0, 0, 0, 0])
        return Context(app, window, displays.display_info(display_id))

    def secure_input(self):
        return session.secure_input()
