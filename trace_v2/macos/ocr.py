"""Vision OCR reader for trace_v2.observations.pipeline (`sources.ocr`).

Reads the exact PNG bytes of the capture's screenshot (PNG data -> CGImageSource -> CGImage, no temp file, no second
capture) with VNRecognizeTextRequest: accurate level, language correction on, automatic language detection when the OS
has it (macOS 13+).

raw   {"lines": [{"text", "confidence", "box": [x, y, w, h]}], "image_size": [w, h], "recognition_level",
       "languages", "os_version"}   box is in image pixels, top-left origin; lines are in reading order.
text  the lines joined with "\\n".

Reading order is row based: lines are sorted top to bottom, lines whose vertical centres fall within one row's span
are ordered left to right. Multi-column layouts therefore interleave columns row by row; `raw` keeps every box so a
consumer can re-order. Timestamps come from pipeline.run_reader, which wraps every `read`.

Vision runs on a worker thread so a stuck request cannot stall the capture loop. After `timeout_s` the request is
cancelled and `Timeout` is raised (recorded as a failed result with error_code "Timeout"); a worker that has not
finished yet makes the next read fail fast with the same code instead of piling up threads.

Vision is imported lazily; when it cannot be loaded `read` raises ReaderUnavailable. Only trace_v2.macos imports pyobjc.
"""

import platform
import threading

from trace_v2.observations.pipeline import ReaderUnavailable

READER_VERSION = "vision-ocr/1"
DEFAULT_TIMEOUT_S = 10.0
RECOGNITION_LEVEL = "accurate"
_CONFIDENCE_DIGITS, _BOX_DIGITS = 3, 1


class Timeout(TimeoutError):
    """Recognition did not finish in time. The class name is the recorded error_code."""


def _pixel_box(rect, width, height):
    """Vision's normalised rect (origin bottom-left) -> [x, y, w, h] in pixels with a top-left origin."""
    x, y = rect.origin.x * width, (1.0 - rect.origin.y - rect.size.height) * height
    w, h = rect.size.width * width, rect.size.height * height
    x0, y0 = max(0.0, min(x, width)), max(0.0, min(y, height))
    x1, y1 = max(x0, min(x + w, width)), max(y0, min(y + h, height))
    return [round(v, _BOX_DIGITS) for v in (x0, y0, x1 - x0, y1 - y0)]


def lines_from_observations(observations, width, height):
    """Observations (VNRecognizedTextObservation or look-alikes) -> unordered line dicts. Empty text is dropped."""
    lines = []
    for obs in observations or ():
        candidates = obs.topCandidates_(1)
        if not candidates:
            continue
        text = str(candidates[0].string())
        if not text.strip():
            continue
        lines.append({"text": text, "confidence": round(float(candidates[0].confidence()), _CONFIDENCE_DIGITS),
                      "box": _pixel_box(obs.boundingBox(), width, height)})
    return lines


def reading_order(lines):
    """Row by row, top to bottom; left to right within a row. Deterministic for equal positions."""
    rows = []
    for line in sorted(lines, key=lambda ln: (ln["box"][1], ln["box"][0])):
        top, bottom = line["box"][1], line["box"][1] + line["box"][3]
        centre = (top + bottom) / 2
        if rows and rows[-1]["top"] <= centre <= rows[-1]["bottom"]:
            rows[-1]["lines"].append(line)
            rows[-1]["bottom"] = max(rows[-1]["bottom"], bottom)
        else:
            rows.append({"top": top, "bottom": bottom, "lines": [line]})
    return [ln for row in rows for ln in sorted(row["lines"], key=lambda ln: ln["box"][0])]


class Recognition:
    """What a recognizer returns: Vision observations plus the decoded image size and the languages in effect."""

    def __init__(self, observations, width, height, languages):
        self.observations, self.width, self.height, self.languages = observations, width, height, list(languages)


class VisionRecognizer:
    """PNG bytes -> Recognition, using Vision. Holds the running request so `cancel` can stop it."""

    def __init__(self):
        self._request = None

    def cancel(self):
        request = self._request
        if request is not None:
            request.cancel()

    def __call__(self, png):
        try:
            import Foundation
            import Quartz
            import Vision
        except ImportError as e:
            raise ReaderUnavailable(f"Vision is not available: {e}") from e
        source = Quartz.CGImageSourceCreateWithData(Foundation.NSData.dataWithBytes_length_(png, len(png)), None)
        image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None) if source is not None else None
        if image is None:
            raise ValueError("screenshot bytes could not be decoded as an image")
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        if request.respondsToSelector_(b"setAutomaticallyDetectsLanguage:"):
            request.setAutomaticallyDetectsLanguage_(True)
        languages = [str(v) for v in (request.recognitionLanguages() or ())]
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
        self._request = request
        try:
            ok, error = handler.performRequests_error_([request], None)
        finally:
            self._request = None
        if not ok:
            raise RuntimeError(f"Vision text recognition failed: {error}")
        return Recognition(list(request.results() or ()), Quartz.CGImageGetWidth(image),
                           Quartz.CGImageGetHeight(image), languages)


class VisionOCR:
    version = READER_VERSION

    def __init__(self, timeout_s=DEFAULT_TIMEOUT_S, recognizer_factory=VisionRecognizer, os_version=None):
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.timeout_s = timeout_s
        self._factory = recognizer_factory
        self._os_version = os_version or platform.mac_ver()[0]
        self._worker = None

    def read(self, screenshot, context=None):
        """-> (raw, text) for the screenshot's own bytes."""
        if screenshot.format != "png":
            raise ValueError(f"OCR reads PNG screenshots, not {screenshot.format!r}")
        recognition = self._recognize(bytes(screenshot.data))
        lines = reading_order(lines_from_observations(recognition.observations, recognition.width,
                                                      recognition.height))
        raw = {"lines": lines, "image_size": [recognition.width, recognition.height],
               "recognition_level": RECOGNITION_LEVEL, "languages": recognition.languages,
               "os_version": self._os_version}
        return raw, "\n".join(ln["text"] for ln in lines)

    def _recognize(self, png):
        if self._worker is not None and self._worker.is_alive():
            raise Timeout("a previous recognition is still running")
        recognizer, box, done = self._factory(), {}, threading.Event()

        def work():
            try:
                import objc
                with objc.autorelease_pool():
                    box["value"] = recognizer(png)
            except BaseException as e:               # noqa: BLE001 - re-raised on the caller's thread
                box["error"] = e
            finally:
                done.set()

        self._worker = threading.Thread(target=work, name="trace-v2-vision-ocr", daemon=True)
        self._worker.start()
        if not done.wait(self.timeout_s):
            cancel = getattr(recognizer, "cancel", None)
            if cancel:
                cancel()
            raise Timeout(f"text recognition did not finish in {self.timeout_s}s")
        if "error" in box:
            raise box["error"]
        return box["value"]
