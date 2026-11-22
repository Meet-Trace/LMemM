"""Trace V2 macOS adapters. The ONLY package that imports pyobjc; import it on macOS only.

Public interface:
    MacSources()          the `sources` object trace_v2.observations.pipeline expects (context, screenshot, Vision
                          OCR, accessibility)
    ContextSource         metadata-only context: frontmost app, front window (+ browser url/private), display
    ScreenshotSource      full-resolution lossless PNG of one display
    AccessibilityReader   AX tree of the front window under node/depth/time budgets (reader ax-tree/1)
    InputTap              listen-only CGEventTap -> EventBuffer (Input Monitoring; sampled cursor, exact clicks/scrolls/keys)
    VisionOCR             Vision text recognition of the capture's own screenshot bytes (reader vision-ocr/1), in process
    OcrWorkerClient       the same reader interface, running Vision in a separate killable worker process

The names are imported on first use (PEP 562), not when the package is imported. Importing a submodule such as
`trace_v2.macos.ocr_worker` (the OCR worker's entry point) therefore does not load AppKit, Quartz or the accessibility
code: the worker stays small, and the OCR client and protocol import on any platform.
"""

import importlib

_EXPORTS = {"AccessibilityReader": ".accessibility", "ContextSource": ".context", "InputTap": ".input_tap",
            "VisionOCR": ".ocr", "OcrWorkerClient": ".ocr_client", "ScreenshotSource": ".screenshot",
            "MacSources": ".sources"}

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    if name in _EXPORTS:
        value = getattr(importlib.import_module(_EXPORTS[name], __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
