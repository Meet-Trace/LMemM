"""Shared builders for the Trace V2 Observation tests. Synthetic data only."""

import tempfile
from pathlib import Path

from trace_v2.observations import (CaptureDraft, Context, InteractionEvent, ObservationStore, ReaderResult,
                                   ScreenshotCapture)

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
T0 = 1_800_000_000_000_000_000          # 2027-01-15, fixed so tests never depend on the clock
SEC = 1_000_000_000
DAY = 86400 * SEC


def context(app="com.example.editor"):
    return Context({"bundle_id": app, "name": "Editor", "pid": 42}, {"title": "notes.txt", "id": 7},
                   {"id": "main", "bounds": [0, 0, 1440, 900], "scale": 2.0})


def ocr_ok(text="hello world"):
    return ReaderResult("vision-ocr/1", "succeeded",
                        {"lines": [{"text": text, "confidence": 0.97, "box": [10, 20, 300, 18]}]}, text, None, None,
                        T0, T0 + 5)


def ax_ok():
    tree = {"role": "AXWindow", "title": "notes.txt", "children": [
        {"role": "AXTextArea", "value": "hello world", "frame": [0, 0, 800, 600], "children": []}]}
    return ReaderResult("ax-tree/1", "succeeded", tree, "hello world")


def failed(version, code="ReaderCrashed"):
    return ReaderResult(version, "failed", error_code=code, error_message="synthetic failure")


def click(t, x=1.0, y=2.0):
    return InteractionEvent("click", t, {"x": x, "y": y, "button": "left", "phase": "down", "click_count": 1})


def draft(key="session:0", t=T0, ocr=None, ax=None, shot=None, events=(), app="com.example.editor"):
    return CaptureDraft(key, t, context(app), shot or ScreenshotCapture(PNG, "png", 32, 32, t), ocr or ocr_ok(),
                        ax or ax_ok(), "test-capture/1", tuple(events), (None, t), {"received": len(events)})


class TempStore:
    """Context manager: an ObservationStore in a fresh directory (and a legacy archive path outside it)."""

    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory(prefix="trace-v2-")
        self.root = Path(self._dir.name) / "data"
        self.legacy = Path(self._dir.name) / "Legacy-V1"
        self.store = ObservationStore(self.root, legacy_archive=self.legacy)
        return self

    def reopen(self):
        self.store.close()
        self.store = ObservationStore(self.root, legacy_archive=self.legacy)
        return self.store

    def __exit__(self, *exc):
        self.store.close()
        self._dir.cleanup()
