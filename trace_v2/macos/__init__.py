"""Trace V2 macOS adapters. The ONLY package that imports pyobjc; import it on macOS only.

Public interface:
    MacSources()          the `sources` object trace_v2.observations.pipeline expects (context, screenshot, Vision
                          OCR; the accessibility reader is not implemented yet and reports ReaderUnavailable)
    ContextSource         metadata-only context: frontmost app, front window (+ browser url/private), display
    ScreenshotSource      full-resolution lossless PNG of one display
    VisionOCR             Vision text recognition of the capture's own screenshot bytes (reader vision-ocr/1)
"""

from .context import ContextSource
from .ocr import VisionOCR
from .screenshot import ScreenshotSource
from .sources import MacSources

__all__ = ["ContextSource", "MacSources", "ScreenshotSource", "VisionOCR"]
