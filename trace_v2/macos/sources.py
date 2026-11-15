"""MacSources: the `sources` object for trace_v2.observations.pipeline (context + screenshot in PR A)."""

import Quartz
from trace_v2.observations.pipeline import ReaderUnavailable

from .context import ContextSource
from .screenshot import ScreenshotSource

CAPTURE_VERSION = "macos-capture/1"


class _NotImplementedReader:
    def __init__(self, version):
        self.version = version

    def read(self, *args):
        raise ReaderUnavailable("not implemented in PR A")


class MacSources:
    capture_version = CAPTURE_VERSION

    def __init__(self, context_source=None, screenshot_source=None):
        self._context = context_source or ContextSource()
        self._screenshot = screenshot_source or ScreenshotSource()
        self.ocr = _NotImplementedReader("ocr-unimplemented/0")
        self.accessibility = _NotImplementedReader("ax-unimplemented/0")
        self._display_id = None

    def context(self):
        self._display_id = None
        context = self._context.context()
        self._display_id = context.display.get("id")
        return context

    def secure_input(self):
        return self._context.secure_input()

    def screenshot(self):
        """The display chosen by the latest context() call; the main display if there was none."""
        return self._screenshot.screenshot(self._display_id or int(Quartz.CGMainDisplayID()))
