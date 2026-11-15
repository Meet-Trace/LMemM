"""Trace V2 Observation layer: immutable, unified snapshots of the user's screen and interactions.

Public interface:
    ObservationStore(root)              open (and migrate) the store in a V2 data directory
    store.ingest(CaptureDraft)          record one Observation atomically
    store.get / list / events / screenshot_bytes / extractions / audit
    store.add_extraction(...)           append a reader re-run (the original is kept)
    retention.set_policy / get_policy / run
    forget.forget / resume_pending      user-requested, audited deletion (the only way evidence is deleted)
    privacy.gate / redact / load_policy / save_policy   applied before ingestion by the pipeline
    events.EventBuffer                  validated, privacy-safe raw interaction events
    pipeline.build_draft / observe_once / ContinuousObserver
Independent of any inference layer: nothing here reads Moments, Visits, Activities or Projects.
"""

from .models import (CaptureDraft, Context, InteractionEvent, InvalidCapture, ReaderResult, ScopeUnavailable,
                     ScreenshotCapture, ScreenshotFailure, ScreenshotWithheld)
from .queries import ScreenshotUnavailable
from .store import DuplicateObservation, IngestResult, ObservationStore

__all__ = ["CaptureDraft", "Context", "DuplicateObservation", "IngestResult", "InteractionEvent", "InvalidCapture",
           "ObservationStore", "ReaderResult", "ScopeUnavailable", "ScreenshotCapture", "ScreenshotFailure",
           "ScreenshotUnavailable", "ScreenshotWithheld"]
