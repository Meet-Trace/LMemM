"""Measure real V2 capture cost: N observations at the capture interval through the real pipeline, with Vision OCR.

    .venv/bin/python scripts/v2_measure_capture.py [--count 40] [--interval 3.0] [--ocr-timeout 10] [--keep]

Writes only to a fresh temp directory, deleted at the end unless --keep. Never touches V1 data or the real V2 store.
The run is driven by ContinuousObserver, so missed ticks (a capture that overran its interval) are counted exactly as
they would be in production. One unmeasured warm-up OCR runs first, with a long timeout, because the first Vision call
on a machine can take a minute or more (model compilation) and would otherwise be recorded as a Timeout.

Prints: capture latency p50/p95/max, screenshot bytes and projected GB per 8 h, OCR latency p50/p95/max (from the stored
reader timestamps), CPU seconds per OCR and per capture (this process only; Vision's own XPC/ANE work is not counted),
peak RSS, text characters per capture, compressed raw OCR bytes per capture, projected GB per 8 h of structured
evidence, and missed ticks.
"""

import argparse
import resource
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trace_v2.macos import MacSources, VisionOCR           # noqa: E402
from trace_v2.macos.ocr import DEFAULT_TIMEOUT_S           # noqa: E402
from trace_v2.observations import ObservationStore         # noqa: E402
from trace_v2.observations.events import EventBuffer       # noqa: E402
from trace_v2.observations.pipeline import ContinuousObserver  # noqa: E402
from trace_v2.observations.settings import DEFAULT_CAPTURE_INTERVAL_S  # noqa: E402

WARMUP_TIMEOUT_S = 300.0
DAY_S = 8 * 3600


def percentile(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p * (len(ordered) - 1)))]


class MeasuredOCR:
    """Wraps the OCR reader and records process CPU and wall time per read (the pipeline thread waits while it runs)."""

    def __init__(self, reader):
        self._reader, self.version = reader, reader.version
        self.cpu, self.wall = [], []

    def read(self, *args):
        wall, proc = time.perf_counter(), time.process_time()
        try:
            return self._reader.read(*args)
        finally:
            self.cpu.append(time.process_time() - proc)
            self.wall.append(time.perf_counter() - wall)


def warm_up(sources):
    """-> seconds the first Vision call took, or None when it could not run. Not stored."""
    start = time.perf_counter()
    try:
        VisionOCR(timeout_s=WARMUP_TIMEOUT_S).read(sources.screenshot(), None)
    except Exception as e:                                  # noqa: BLE001 - reported, the measurement still runs
        print(f"warm-up failed: {type(e).__name__}: {e}")
        return None
    return time.perf_counter() - start


def collect(store, observation_ids):
    rows = []
    for oid in observation_ids:
        obs = store.get(oid, include_raw=False, include_events=False)
        ocr, shot = obs["evidence"]["ocr"], obs["evidence"]["screenshot"]
        blob = store.db.execute("SELECT length(raw) FROM extractions WHERE observation_id=? AND reader='ocr'",
                                (oid,)).fetchone()[0]
        started, finished = ocr["started_at_ns"], ocr["finished_at_ns"]
        rows.append({"shot": shot["status"], "shot_bytes": shot["byte_size"], "ocr": ocr["status"],
                     "ocr_code": ocr["error_code"], "chars": len(ocr["text"] or ""), "raw_zlib": blob or 0,
                     "ocr_s": (finished - started) / 1e9 if started and finished else None,
                     "withheld": bool(obs["metadata"].get("privacy", {}).get("withheld"))})
    return rows


def run(count, interval, root, ocr_timeout):
    store = ObservationStore(root / "data", legacy_archive=root / "Legacy-V1")
    ocr = MeasuredOCR(VisionOCR(timeout_s=ocr_timeout))
    sources, buffer = MacSources(ocr=ocr), EventBuffer(start_ns=time.time_ns())
    warmup = warm_up(sources)
    observer = ContinuousObserver(store, sources, buffer, interval_s=interval)
    ticks, tick = [], observer.tick

    def timed_tick():
        wall, proc = time.perf_counter(), time.process_time()
        ok = tick()
        ticks.append((time.perf_counter() - wall, time.process_time() - proc))
        return ok
    observer.tick = timed_tick
    stats = observer.run(max_ticks=count)
    rows = collect(store, [r["observation_id"] for r in store.list(limit=1000)])
    store.close()
    return {"warmup": warmup, "ticks": ticks, "ocr": ocr, "rows": rows, "stats": stats, "api": sources._screenshot.last_path}


def ms(values):
    return f"p50 {percentile(values, .5) * 1000:.0f}  p95 {percentile(values, .95) * 1000:.0f}  max {max(values) * 1000:.0f}"


def report(count, interval, m):
    rows, ticks, stats = m["rows"], m["ticks"], m["stats"]
    stored = [r for r in rows if r["shot"] == "stored"]
    ok = [r for r in rows if r["ocr"] == "succeeded"]
    per_day = DAY_S / interval
    print(f"captures: {count} at {interval}s  stored screenshots: {len(stored)}  withheld: "
          f"{sum(r['withheld'] for r in rows)}  failed: {stats['failures']}  screenshot API: {m['api']}")
    warmup = "n/a" if m["warmup"] is None else f"{m['warmup']:.1f} s"
    print(f"warm-up OCR: {warmup} (unmeasured)")
    print(f"missed ticks: {stats['missed_ticks']} of {count}  "
          f"({'3 s keeps up' if not stats['missed_ticks'] else 'the interval is NOT kept'} at {interval}s)")
    print(f"capture latency ms  {ms([w for w, _ in ticks])}")
    print(f"cpu s/capture  mean {statistics.mean(c for _, c in ticks):.3f}  max {max(c for _, c in ticks):.3f}")
    outcomes = {}
    for r in rows:
        key = r["ocr"] + (f"/{r['ocr_code']}" if r["ocr_code"] else "")
        outcomes[key] = outcomes.get(key, 0) + 1
    print(f"OCR outcomes: {outcomes}")
    if ok:
        ocr_s = [r["ocr_s"] for r in ok]
        print(f"OCR latency ms  {ms(ocr_s)}")
        cpu = m["ocr"].cpu
        print(f"OCR cpu s/read  mean {statistics.mean(cpu):.3f}  max {max(cpu):.3f}  "
              f"(~{statistics.mean(cpu) / interval * 100:.0f}% of one core at {interval}s)")
        chars = [r["chars"] for r in ok]
        print(f"text chars/capture  mean {statistics.mean(chars):,.0f}  min {min(chars):,}  max {max(chars):,}")
        raw = [r["raw_zlib"] for r in ok]
        structured = statistics.mean(r["raw_zlib"] + r["chars"] for r in ok)
        print(f"raw OCR zlib bytes/capture  mean {statistics.mean(raw):,.0f}  min {min(raw):,}  max {max(raw):,}")
        print(f"structured evidence  {structured:,.0f} B/capture (compressed raw + text)  ->  "
              f"{structured * per_day / 1e9:.2f} GB per 8 h at {interval}s")
    if stored:
        sizes = [r["shot_bytes"] for r in stored]
        print(f"PNG bytes   mean {statistics.mean(sizes):,.0f}  min {min(sizes):,}  max {max(sizes):,}  ->  "
              f"{statistics.mean(sizes) * per_day / 1e9:.2f} GB per 8 h at {interval}s (stored captures only)")
    print(f"peak RSS    {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6:.0f} MB")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--interval", type=float, default=DEFAULT_CAPTURE_INTERVAL_S)
    parser.add_argument("--ocr-timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--keep", action="store_true", help="keep the temp directory")
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="trace-v2-measure-")).resolve()
    try:
        report(args.count, args.interval, run(args.count, args.interval, root, args.ocr_timeout))
    finally:
        if args.keep:
            print(f"kept: {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
