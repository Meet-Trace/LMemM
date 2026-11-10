"""LMemM - where closed moments are kept: one append-only JSONL file per day, 48 hours, owner-only.

Under <memory_dir>/moments/. `forget.delete_all` removes the whole data folder, so it covers this too;
`forget(...)` here is the partial form (the last N minutes, or one app).
"""

import json
import os
import threading
import time

import config

DIR_MODE, FILE_MODE = 0o700, 0o600


def moments_dir():
    return os.path.join(config.paths().memory_dir, "moments")


def _day(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


class MomentStore:
    def __init__(self, directory=None, retention_hours=None):
        self.dir = directory or moments_dir()
        self.retention = (retention_hours if retention_hours is not None else config.MOMENT_RETENTION_HOURS) * 3600.0
        self.lock = threading.Lock()

    def _path(self, day):
        return os.path.join(self.dir, day + ".jsonl")

    def append(self, moment):
        """Write one closed moment. Never raises into the caller's loop beyond OSError."""
        rec = json.dumps(moment.to_dict(), separators=(",", ":"), ensure_ascii=False)
        with self.lock:
            os.makedirs(self.dir, mode=DIR_MODE, exist_ok=True)
            fd = os.open(self._path(_day(moment.t_start)), os.O_WRONLY | os.O_APPEND | os.O_CREAT, FILE_MODE)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(rec + "\n")

    def read(self, since=None, until=None):
        """Stored moments as dicts, oldest first, within [since, until] (by t_start)."""
        out = []
        with self.lock:
            for name in sorted(self._files()):
                for rec in self._records(os.path.join(self.dir, name)):
                    if (since is None or rec.get("t_end", 0) >= since) and (until is None or rec.get("t_start", 0) <= until):
                        out.append(rec)
        out.sort(key=lambda r: (r.get("t_start", 0), r.get("seq", 0)))
        return out

    def sweep(self, now=None):
        """Drop records older than the retention window; delete files that become empty. Returns how many were dropped."""
        now = now or time.time()
        cutoff = now - self.retention
        dropped = 0
        with self.lock:
            for name in self._files():
                path = os.path.join(self.dir, name)
                recs = list(self._records(path))
                keep = [r for r in recs if r.get("t_end", 0) >= cutoff]
                if len(keep) == len(recs):
                    continue
                dropped += len(recs) - len(keep)
                self._rewrite(path, keep)
        return dropped

    def forget(self, minutes=None, app=None, now=None):
        """Delete the last `minutes` of moments (None = everything), optionally only one app (name or bundle id)."""
        now = now or time.time()
        since = now - minutes * 60 if minutes is not None else None
        dropped = 0
        with self.lock:
            for name in self._files():
                path = os.path.join(self.dir, name)
                recs = list(self._records(path))
                keep = [r for r in recs
                        if not ((since is None or r.get("t_end", 0) >= since)
                                and (app is None or app in (r.get("app"), r.get("bundle_id"))))]
                if len(keep) != len(recs):
                    dropped += len(recs) - len(keep)
                    self._rewrite(path, keep)
        return dropped

    # ------------------------------------------------------------ internals

    def _files(self):
        try:
            return [n for n in os.listdir(self.dir) if n.endswith(".jsonl")]
        except OSError:
            return []

    @staticmethod
    def _records(path):
        try:
            with open(path, encoding="utf-8") as fh:
                for ln in fh:
                    try:
                        yield json.loads(ln)
                    except ValueError:
                        continue            # a half-written last line after a crash
        except OSError:
            return

    @staticmethod
    def _rewrite(path, recs):
        if not recs:
            try:
                os.remove(path)
            except OSError:
                pass
            return
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n")
        os.replace(tmp, path)


class Recorder:
    """What the tracker holds: a MomentBuffer wired to a MomentStore from config. `frame` never raises."""

    def __init__(self, store=None):
        import moments
        self.store = store or MomentStore()
        self.buffer = moments.MomentBuffer(
            sink=self.store.append, visit_gap=config.MOMENT_VISIT_GAP, min_category_s=config.MOMENT_MIN_CATEGORY_S,
            max_moment_s=config.MOMENT_MAX_S, new_terms=config.MOMENT_NEW_TERMS, window_s=config.MOMENT_WINDOW_S,
            max_moments=config.MOMENT_MAX, keep_lines=config.MOMENT_KEEP_LINES)

    def frame(self, now, item_id, meta, st, ref, act, dt, content, pinned):
        """One resolved frame, read-only: what the tracker already knows about it."""
        try:
            self.buffer.observe(
                now, item_id, app=st.get("app"), bundle_id=meta.get("bundle_id"), window=meta.get("window"),
                url=meta.get("url"), kind=st.get("kind"), target=st.get("target"), ref=ref,
                category=act.get("category"), ages=meta.get("inputs"), text=(content or {}).get("text") or "",
                provenance=(content or {}).get("provenance"), trigger=meta.get("trigger"), pinned=pinned, dt=dt)
        except Exception as e:           # shadow mode: a bug here must never touch the tracker
            print(f"moments: {e!r}")

    def forget(self, minutes=None, app=None, now=None):
        """Partial forget: memory first (the open moment included, never written), then disk. Returns (memory, disk)."""
        now = now or time.time()
        since = now - minutes * 60 if minutes is not None else None
        with self.buffer.lock:                      # a frame cannot slip a forgotten moment back in between the two
            mem = self.buffer.forget(since, app)
            disk = self.store.forget(minutes, app, now)
        return mem, disk

    def sweep(self, now=None):
        try:
            self.buffer.idle_flush(now or time.time())
            return self.store.sweep(now)
        except Exception as e:
            print(f"moments: {e!r}")
            return 0

    def close(self):
        try:
            self.buffer.close()
        except Exception as e:
            print(f"moments: {e!r}")
