"""Trace V2 macOS adapters. The ONLY package that imports pyobjc; import it on macOS only.

Public interface:
    MacSources()          the `sources` object trace_v2.observations.pipeline expects (context, screenshot, Vision
                          OCR, accessibility)
    ContextSource         metadata-only context: frontmost app, front window (+ browser url/private), display
    ScreenshotSource      full-resolution lossless PNG of one display
    AccessibilityReader   AX tree of the front window under node/depth/time budgets (reader ax-tree/1)
    InputTap              listen-only CGEventTap -> EventBuffer (Input Monitoring; sampled cursor, exact clicks/scrolls/keys)
    VisionOCR             Vision text recognition of the capture's own screenshot bytes (reader vision-ocr/1)
"""

from .accessibility import AccessibilityReader
from .context import ContextSource
from .input_tap import InputTap
from .ocr import VisionOCR
from .screenshot import ScreenshotSource
from .sources import MacSources

__all__ = ["AccessibilityReader", "ContextSource", "InputTap", "MacSources", "ScreenshotSource", "VisionOCR"]
