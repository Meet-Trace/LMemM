"""Trace V2 macOS adapters. The ONLY package that imports pyobjc; import it on macOS only.

Public interface:
    MacSources()          the `sources` object trace_v2.observations.pipeline expects (context + screenshot;
                          OCR and accessibility readers are not implemented yet and report ReaderUnavailable)
    ContextSource         metadata-only context: frontmost app, front window (+ browser url/private), display
    ScreenshotSource      full-resolution lossless PNG of one display
"""

from .context import ContextSource
from .screenshot import ScreenshotSource
from .sources import MacSources

__all__ = ["ContextSource", "MacSources", "ScreenshotSource"]
