"""MacSources: the `sources` object for trace_v2.observations.pipeline (context, screenshot, Vision OCR, accessibility)."""

import Quartz
from trace_v2.observations.settings import DEFAULT_SCREENSHOT_SCOPE, SCREENSHOT_SCOPES

from .accessibility import AccessibilityReader
from .context import ContextSource
from .ocr import VisionOCR
from .screenshot import ScreenshotSource

CAPTURE_VERSION = "macos-capture/1"   # unchanged: the capture shape is the same, OCR is a reader with its own version


class MacSources:
    capture_version = CAPTURE_VERSION

    def __init__(self, context_source=None, screenshot_source=None, ocr=None, accessibility=None,
                 screenshot_scope=DEFAULT_SCREENSHOT_SCOPE):
        self._context = context_source or ContextSource()
        self._screenshot = screenshot_source or ScreenshotSource()
        self.ocr = ocr or VisionOCR()
        self.accessibility = accessibility or AccessibilityReader()
        self._display_id = None
        self._application = {}
        self.screenshot_scope = screenshot_scope        # settable while running; the runner refreshes it from settings

    @property
    def screenshot_scope(self):
        return self._scope

    @screenshot_scope.setter
    def screenshot_scope(self, scope):
        if scope not in SCREENSHOT_SCOPES:
            raise ValueError(f"unknown screenshot scope {scope!r}")
        self._scope = scope

    def context(self):
        self._display_id, self._application = None, {}
        context = self._context.context()
        self._display_id = context.display.get("id")
        self._application = context.application or {}
        return context

    def secure_input(self):
        return self._context.secure_input()

    def screenshot(self):
        """The display chosen by the latest context() call (the main display if there was none), limited to the
        configured scope. In front_app scope the application is the one that context() reported, i.e. the very app the
        privacy gates judged, even if the user switched apps in between."""
        return self._screenshot.screenshot(self._display_id or int(Quartz.CGMainDisplayID()), scope=self._scope,
                                           pid=self._application.get("pid"), bundle_id=self._application.get("bundle_id"))

    def probe(self):
        """Tiny PNG of what screenshot() would capture now, to tell whether the screen changed (see observations.trigger)."""
        return self._screenshot.probe(self._display_id or int(Quartz.CGMainDisplayID()), scope=self._scope,
                                      pid=self._application.get("pid"), bundle_id=self._application.get("bundle_id"))
