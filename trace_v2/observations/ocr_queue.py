"""Durable OCR job queue. Operational state (table ocr_jobs), not evidence: the OCR result itself is appended to the
Observation as the next `extractions` revision, never stored here.

Life of a job: queued -> processing (claim) -> done | failed, or back to queued (retry, deferral, lost lease, release).
Every transition checks the job's state and claim_token inside one transaction, so a result from a restarted or replaced
worker, a reclaimed job, a finished job or a forgotten observation is refused ("stale" / "gone") and never written."""

import logging
import uuid
from dataclasses import dataclass

from . import pipeline, privacy
from .assets import AssetError
from .models import ReaderResult, ScreenshotCapture
from .store import canonical, now_iso

log = logging.getLogger("trace_v2.ocr_queue")

TRANSIENT = frozenset({"Timeout", "WorkerCrashed", "WorkerBusy", "WorkerProtocolError", "CompletionFailed"})
DEFERRED = frozenset({"WorkerStarting", "WorkerCircuitOpen", "WorkerClosed"})
LATE_REASON = "secret_on_screen_late"
UNSCANNED_REASON = "unscanned"
PRUNE_AFTER_S = 7 * 86400
S = 1_000_000_000


@dataclass(frozen=True)
class QueuePolicy:
    max_attempts: int = 4
    backoff_base_s: float = 5.0
    backoff_cap_s: float = 120.0
    deferral_s: float = 3.0
    max_deferrals: int = 200
    lease_slack_s: float = 15.0
    max_age_s: float = 3600.0


@dataclass(frozen=True)
class Job:
    job_id: int
    observation_id: str
    target_revision: int
    claim_token: str
    attempts: int
    deferrals: int
    max_attempts: int


class JobError(Exception):
    """The job cannot run (deterministic): `code` becomes the error_code of the final failed revision."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


class _Txn:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        self.store.lock.acquire()
        try:
            self.store.db.execute("BEGIN IMMEDIATE")
        except BaseException:
            self.store.lock.release()
            raise
        return self.store.db

    def __exit__(self, exc_type, *_):
        try:
            if exc_type is None:
                self.store.db.execute("COMMIT")
            else:
                self.store._rollback()
        finally:
            self.store.lock.release()


def enqueue(store, observation_id, now_ns, max_attempts=QueuePolicy.max_attempts):
    """Inside the caller's transaction (the observation's ingest): one job per (observation, target revision)."""
    store.db.execute("INSERT INTO ocr_jobs(observation_id, target_revision, state, max_attempts, not_before_ns, "
                     "enqueued_at_ns) VALUES (?,?,?,?,?,?)", (observation_id, 2, "queued", max_attempts, now_ns, now_ns))


def depth(store):
    with store.lock:
        return store.db.execute("SELECT count(*) FROM ocr_jobs WHERE state IN ('queued','processing')").fetchone()[0]


def claim(store, now_ns, timeout_s, policy=QueuePolicy()):
    with _Txn(store) as db:
        row = db.execute("SELECT job_id, observation_id, target_revision, attempts, deferrals, max_attempts FROM ocr_jobs "
                         "WHERE state='queued' AND not_before_ns<=? ORDER BY job_id LIMIT 1", (now_ns,)).fetchone()
        if row is None:
            return None
        token = str(uuid.uuid4())
        lease = now_ns + int((timeout_s + policy.lease_slack_s) * S)
        db.execute("UPDATE ocr_jobs SET state='processing', claim_token=?, claimed_at_ns=?, lease_expires_ns=?, "
                   "attempts=attempts+1 WHERE job_id=?", (token, now_ns, lease, row[0]))
        return Job(row[0], row[1], row[2], token, row[3] + 1, row[4], row[5])


def load_screenshot(store, job):
    """The stored PNG of the job's observation, read (and hash-verified) outside the store lock."""
    with store.lock:
        row = store.db.execute(
            "SELECT s.status, s.relative_path, s.sha256, s.format, s.width, s.height, s.captured_at_ns, d.observation_id "
            "FROM screenshots s LEFT JOIN screenshot_deletions d USING (observation_id) WHERE s.observation_id=?",
            (job.observation_id,)).fetchone()
    if row is None:
        raise JobError("ScreenshotMissing", "no screenshot row")
    status, rel, digest, fmt, width, height, captured, deleted = row
    if status != "stored" or deleted:
        raise JobError("ScreenshotUnavailable", "the screenshot is not stored")
    try:
        data = store.assets.read(rel, digest)
    except FileNotFoundError:
        raise JobError("ScreenshotMissing", "the screenshot file is gone") from None
    except AssetError as error:
        raise JobError("ScreenshotCorrupt", str(error)) from None
    return ScreenshotCapture(data, fmt, width, height, captured)


def _owned(db, job):
    row = db.execute("SELECT state, claim_token FROM ocr_jobs WHERE job_id=?", (job.job_id,)).fetchone()
    if row is None:
        return "gone"
    return "ok" if row[0] == "processing" and row[1] == job.claim_token else "stale"


def _latest(db, oid):
    return db.execute("SELECT extraction_id, revision, reader_version FROM extractions WHERE observation_id=? "
                      "AND reader='ocr' ORDER BY revision DESC LIMIT 1", (oid,)).fetchone()


def _append(store, db, oid, target, result, privacy_json=None):
    """Append the OCR revision to the observation. -> 'ok', or 'superseded' when another revision got there first."""
    prev = _latest(db, oid)
    if prev is None:
        return "gone"
    if prev[1] != target - 1:
        return "superseded"
    store._insert_extraction(oid, "ocr", result, target, prev[0], privacy_json)
    return "ok"


def _terminal(store, db, job_id, oid, target, code, message, now_ns, version=None):
    prev = _latest(db, oid)
    result = ReaderResult(version or (prev[2] if prev else "unknown"), "failed", None, None, code, message, None, now_ns)
    outcome = _append(store, db, oid, target, result)
    if outcome == "ok":
        _withhold(store, db, oid, now_ns, UNSCANNED_REASON, only_quarantined=True)
    db.execute("UPDATE ocr_jobs SET state='failed', claim_token=NULL, finished_at_ns=?, last_error_code=? WHERE job_id=?",
               (now_ns, code if outcome == "ok" else outcome, job_id))
    return "failed"


def finish(store, job, result, now_ns, policy=QueuePolicy()):
    """Record the reader's outcome for a claimed job. -> completed | retried | deferred | failed | stale | gone | superseded."""
    try:
        if result.status == "succeeded":
            return _complete(store, job, result, now_ns)
        return _fail(store, job, result.error_code or "unknown", result.error_message, result.reader_version, now_ns, policy)
    except Exception:                                   # noqa: BLE001 - e.g. the late screenshot removal failed
        log.exception("OCR completion failed for job %s", job.job_id)
        return _fail(store, job, "CompletionFailed", "the result could not be recorded", None, now_ns, policy)


def _complete(store, job, result, now_ns):
    counts = {}
    policy = privacy.load_policy(store)
    if policy.redact_secrets:
        result = pipeline._redact_result(result, counts)
    note = canonical({"redactions": counts, "late_withheld": True, "policy_version": privacy.POLICY_VERSION}) if counts else None
    with _Txn(store) as db:
        owner = _owned(db, job)
        if owner != "ok":
            return owner
        outcome = _append(store, db, job.observation_id, job.target_revision, result, note)
        if outcome == "ok" and counts:
            _withhold(store, db, job.observation_id, now_ns, LATE_REASON)
        elif outcome == "ok":
            _release(store, db, job.observation_id)
        db.execute("UPDATE ocr_jobs SET state='done', claim_token=NULL, finished_at_ns=?, last_error_code=? WHERE job_id=?",
                   (now_ns, None if outcome == "ok" else outcome, job.job_id))
        return "completed" if outcome == "ok" else outcome


def _stored_path(db, oid):
    return db.execute("SELECT s.relative_path, s.byte_size FROM screenshots s LEFT JOIN screenshot_deletions d "
                      "USING (observation_id) WHERE s.observation_id=? AND s.status='stored' AND d.observation_id IS NULL",
                      (oid,)).fetchone()


def _release(store, db, oid):
    """The scan found no secret: a quarantined screenshot moves to its final place."""
    row = _stored_path(db, oid)
    if row is not None:
        store.assets.release(row[0])


def _withhold(store, db, oid, now_ns, reason, only_quarantined=False):
    """Remove the screenshot file, audited like a retention run. LATE_REASON: a secret was found in the OCR text.
    UNSCANNED_REASON: the scan could not be completed, so a quarantined file is not kept (`only_quarantined`: a file that
    is already in its final place, from before quarantine existed or from a synchronous capture, is left alone)."""
    row = _stored_path(db, oid)
    if row is None or (only_quarantined and not store.assets.is_quarantined(row[0])):
        return
    run_id = str(uuid.uuid4())
    db.execute("INSERT INTO retention_runs VALUES (?,?,?,?,?,?)",
               (run_id, now_iso(), now_ns, canonical({"mode": reason}), None, 0))
    existed = store.assets.delete(row[0])
    db.execute("INSERT INTO screenshot_deletions VALUES (?,?,?,?,?)", (oid, now_iso(), reason, run_id, int(existed)))
    db.execute("INSERT INTO retention_run_finishes VALUES (?,?,?,?,?,?,?)",
               (run_id, now_iso(), 1, int(existed), int(not existed), 0, (row[1] or 0) if existed else 0))


def _fail(store, job, code, message, version, now_ns, policy):
    with _Txn(store) as db:
        owner = _owned(db, job)
        if owner != "ok":
            return owner
        if code in DEFERRED:
            if job.deferrals + 1 > policy.max_deferrals:
                return _terminal(store, db, job.job_id, job.observation_id, job.target_revision, code, message, now_ns, version)
            return _requeue(db, job, code, now_ns + int(policy.deferral_s * S), job.attempts - 1, 1, "deferred")
        if code in TRANSIENT and job.attempts < job.max_attempts:
            delay = min(policy.backoff_cap_s, policy.backoff_base_s * 2 ** (job.attempts - 1))
            return _requeue(db, job, code, now_ns + int(delay * S), job.attempts, 0, "retried")
        return _terminal(store, db, job.job_id, job.observation_id, job.target_revision, code, message, now_ns, version)


def _requeue(db, job, code, not_before, attempts, deferrals, outcome):
    db.execute("UPDATE ocr_jobs SET state='queued', claim_token=NULL, claimed_at_ns=NULL, lease_expires_ns=NULL, "
               "not_before_ns=?, attempts=?, deferrals=deferrals+?, last_error_code=? WHERE job_id=?",
               (not_before, attempts, deferrals, code, job.job_id))
    return outcome


def release(store, job, now_ns):
    """Give a claimed job back untouched (shutdown): the attempt is refunded and it is due at once."""
    with _Txn(store) as db:
        if _owned(db, job) != "ok":
            return False
        _requeue(db, job, None, now_ns, job.attempts - 1, 0, "released")
        return True


def fail_terminal(store, job, code, message, now_ns):
    with _Txn(store) as db:
        if _owned(db, job) != "ok":
            return "stale"
        return _terminal(store, db, job.job_id, job.observation_id, job.target_revision, code, message, now_ns)


def sweep(store, now_ns, policy=QueuePolicy()):
    """Recover jobs whose lease ran out (dead process, hung reader) and end jobs that outlived max_age_s."""
    out = {"reclaimed": 0, "lease_failed": 0, "expired": 0, "pruned": 0}
    with _Txn(store) as db:
        for jid, oid, target, attempts, maxa in db.execute(
                "SELECT job_id, observation_id, target_revision, attempts, max_attempts FROM ocr_jobs "
                "WHERE state='processing' AND lease_expires_ns<?", (now_ns,)).fetchall():
            if attempts >= maxa:
                _terminal(store, db, jid, oid, target, "LeaseExpired", "the OCR worker did not answer in time", now_ns)
                out["lease_failed"] += 1
            else:
                db.execute("UPDATE ocr_jobs SET state='queued', claim_token=NULL, claimed_at_ns=NULL, lease_expires_ns=NULL, "
                           "not_before_ns=?, last_error_code='LeaseExpired' WHERE job_id=?", (now_ns, jid))
                out["reclaimed"] += 1
        for jid, oid, target in db.execute(
                "SELECT job_id, observation_id, target_revision FROM ocr_jobs WHERE state IN ('queued','processing') "
                "AND enqueued_at_ns<?", (now_ns - int(policy.max_age_s * S),)).fetchall():
            _terminal(store, db, jid, oid, target, "JobExpired", "the OCR job outlived its maximum age", now_ns)
            out["expired"] += 1
        out["pruned"] = db.execute("DELETE FROM ocr_jobs WHERE state IN ('done','failed') AND finished_at_ns<?",
                                   (now_ns - PRUNE_AFTER_S * S,)).rowcount
    return out


def recover_after_restart(store, now_ns, policy=QueuePolicy()):
    """Invalidate claims owned by the previous process before starting a new worker.

    A lease alone is insufficient here: the old process may still return while the
    lease is live. Clearing its claim token makes that result stale immediately.
    """
    out = {"reclaimed": 0, "lease_failed": 0}
    with _Txn(store) as db:
        rows = db.execute(
            "SELECT job_id, observation_id, target_revision, attempts, max_attempts FROM ocr_jobs "
            "WHERE state='processing' ORDER BY job_id"
        ).fetchall()
        for jid, oid, target, attempts, max_attempts in rows:
            if attempts >= max_attempts:
                _terminal(store, db, jid, oid, target, "WorkerRestarted",
                          "the OCR worker restarted after exhausting its attempts", now_ns)
                out["lease_failed"] += 1
            else:
                db.execute(
                    "UPDATE ocr_jobs SET state='queued', claim_token=NULL, claimed_at_ns=NULL, "
                    "lease_expires_ns=NULL, not_before_ns=?, last_error_code='WorkerRestarted' WHERE job_id=?",
                    (now_ns, jid))
                out["reclaimed"] += 1
    return out


def stats(store, now_ns):
    with store.lock:
        states = dict(store.db.execute("SELECT state, count(*) FROM ocr_jobs GROUP BY state").fetchall())
        oldest = store.db.execute("SELECT min(enqueued_at_ns) FROM ocr_jobs WHERE state IN ('queued','processing')").fetchone()[0]
        retried = store.db.execute("SELECT count(*) FROM ocr_jobs WHERE attempts>1 OR deferrals>0").fetchone()[0]
        failed = dict(store.db.execute("SELECT coalesce(last_error_code,'unknown'), count(*) FROM ocr_jobs "
                                       "WHERE state='failed' GROUP BY 1").fetchall())
    return {"queued": states.get("queued", 0), "processing": states.get("processing", 0), "done": states.get("done", 0),
            "failed": states.get("failed", 0), "oldest_pending_age_s": None if oldest is None else round((now_ns - oldest) / S, 1),
            "retried_jobs": retried, "failed_by_code": failed}


def protected(store):
    """Observations whose screenshot must not be deleted yet: a job is queued or processing, or the OCR is still pending."""
    with store.lock:
        return {r[0] for r in store.db.execute(
            "SELECT observation_id FROM ocr_jobs WHERE state IN ('queued','processing') UNION "
            "SELECT e.observation_id FROM extractions e WHERE e.reader='ocr' AND e.status='pending' AND NOT EXISTS "
            "(SELECT 1 FROM extractions n WHERE n.observation_id=e.observation_id AND n.reader='ocr' AND n.revision>e.revision)")}

