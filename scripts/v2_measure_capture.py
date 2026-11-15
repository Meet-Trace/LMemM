"""Measure real V2 capture cost: N observations at the capture interval through the real pipeline, with Vision OCR
and the accessibility reader.

    .venv/bin/python scripts/v2_measure_capture.py [--count 40] [--interval 3.0] [--ocr-timeout 10] [--keep]
    .venv/bin/python scripts/v2_measure_capture.py --ax-apps com.apple.finder,com.microsoft.VSCode [--count 10]

--input also runs the listen-only event tap (Input Monitoring) into the same buffer; --synthetic-input additionally posts
harmless events for the whole run (cursor wiggle bursts, zero-delta scrolls) so there is load without a user.

--stub-capture replaces context, screenshot and the readers with cheap fakes (an ungated app, a tiny PNG), so the event
path can be measured while the screen is locked or without screen permissions; the OCR/AX numbers are then not real.

--ax-apps skips the pipeline and times AccessibilityReader alone on the named running apps' windows (front or not),
for per-app numbers when the frontmost app cannot be chosen. Everything else is the full pipeline.

Writes only to a fresh temp directory, deleted at the end unless --keep. Never touches V1 data or the real V2 store.
The run is driven by ContinuousObserver, so missed ticks (a capture that overran its interval; OCR and AX together) are counted exactly as
they would be in production. One unmeasured warm-up OCR runs first, with a long timeout, because the first Vision call
on a machine can take a minute or more (model compilation) and would otherwise be recorded as a Timeout.

Prints: capture latency p50/p95/max, screenshot bytes and projected GB per 8 h, OCR latency p50/p95/max (from the stored
reader timestamps), CPU seconds per OCR and per capture (this process only; Vision's own XPC/ANE work is not counted),
peak RSS, text characters per capture, compressed raw OCR bytes per capture, projected GB per 8 h of structured
evidence, and missed ticks.
"""

import argparse
import resource
import threading
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trace_v2.macos import AccessibilityReader, InputTap, MacSources, VisionOCR  # noqa: E402
from trace_v2.macos.ocr import DEFAULT_TIMEOUT_S           # noqa: E402
from trace_v2.observations import Context, ObservationStore  # noqa: E402
from trace_v2.observations.events import EventBuffer       # noqa: E402
from trace_v2.observations.pipeline import ContinuousObserver  # noqa: E402
from trace_v2.observations.settings import DEFAULT_CAPTURE_INTERVAL_S  # noqa: E402

WARMUP_TIMEOUT_S = 300.0
DAY_S = 8 * 3600


def percentile(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p * (len(ordered) - 1)))]


class Measured:
    """Wraps a reader; records process CPU and wall time per read (the pipeline thread waits while it runs), and for
    AX the node count, truncation flags and frontmost app of each successful read."""

    def __init__(self, reader):
        self._reader, self.version = reader, reader.version
        self.cpu, self.wall, self.reads = [], [], []

    def read(self, *args):
        wall, proc = time.perf_counter(), time.process_time()
        try:
            raw, text = self._reader.read(*args)
            stats = raw.get("stats") if isinstance(raw, dict) else None
            if stats:
                self.reads.append({"app": raw["app"]["bundle_id"], "nodes": stats["nodes"],
                                   "truncated": sorted(k for k, v in raw["truncated"].items() if v),
                                   "ms": (time.perf_counter() - wall) * 1000, "chars": len(text)})
            return raw, text
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
        ax = obs["evidence"]["accessibility"]
        ax_blob = store.db.execute("SELECT length(raw) FROM extractions WHERE observation_id=? AND reader='accessibility'",
                                   (oid,)).fetchone()[0]
        started, finished = ocr["started_at_ns"], ocr["finished_at_ns"]
        rows.append({"shot": shot["status"], "shot_bytes": shot["byte_size"], "ocr": ocr["status"],
                     "ocr_code": ocr["error_code"], "chars": len(ocr["text"] or ""), "raw_zlib": blob or 0,
                     "ax": ax["status"], "ax_code": ax["error_code"], "ax_zlib": ax_blob or 0,
                     "ax_chars": len(ax["text"] or ""),
                     "ax_s": (ax["finished_at_ns"] - ax["started_at_ns"]) / 1e9 if ax["started_at_ns"] and ax["finished_at_ns"] else None,
                     "ocr_s": (finished - started) / 1e9 if started and finished else None,
                     "withheld": bool(obs["metadata"].get("privacy", {}).get("withheld"))})
    return rows


class StubCapture:
    """Cheap fake capture sources for --stub-capture."""
    version = "stub/1"

    def context(self):
        return Context({"bundle_id": "com.example.stub", "name": "Stub", "pid": 1}, {"title": "stub", "id": 1},
                       {"id": 1, "bounds": [0, 0, 100, 100], "scale": 1.0})

    def secure_input(self):
        return False

    def screenshot(self, display_id):
        from trace_v2.observations import ScreenshotCapture
        return ScreenshotCapture(b"\x89PNG\r\n\x1a\n" + bytes(64), "png", 8, 8, time.time_ns())

    def read(self, *args):
        return {"lines": [], "stats": None}, ""


def synthetic_input(stop):
    """Bursts that look like use: 4 s of small cursor movement at ~125 Hz, 2 s of 60 Hz scroll events (zero deltas, so
    nothing on screen moves), 4 s idle. Nothing is clicked and no key is pressed."""
    import Quartz
    here = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    phase = 0
    while not stop.is_set():
        started = time.monotonic()
        while not stop.is_set() and time.monotonic() - started < (4 if phase % 3 == 0 else 2 if phase % 3 == 1 else 4):
            if phase % 3 == 0:
                point = Quartz.CGPointMake(here.x + (time.monotonic() * 40) % 3, here.y)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                                   Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, point, 0))
                stop.wait(0.008)
            elif phase % 3 == 1:
                Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                                   Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 2, 0, 0))
                stop.wait(1 / 60)
            else:
                stop.wait(0.1)
        phase += 1
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, here, 0))


def run(count, interval, root, ocr_timeout, use_input=False, synthetic=False, stub=False):
    store = ObservationStore(root / "data", legacy_archive=root / "Legacy-V1")
    ocr, ax = Measured(VisionOCR(timeout_s=ocr_timeout)), Measured(AccessibilityReader())
    stubs = StubCapture()
    sources = (MacSources(context_source=stubs, screenshot_source=stubs, ocr=stubs, accessibility=stubs) if stub
               else MacSources(ocr=ocr, accessibility=ax))
    buffer = EventBuffer(start_ns=time.time_ns())
    warmup = None if stub else warm_up(sources)
    observer = ContinuousObserver(store, sources, buffer, interval_s=interval)
    tap, stop_synthetic = (InputTap(buffer) if use_input or synthetic else None), threading.Event()
    if tap:
        print(f"input tap: {tap.start()} {tap.reason or ''}")
        if synthetic and tap.state == "running":
            threading.Thread(target=synthetic_input, args=(stop_synthetic,), daemon=True).start()
    cpu_before, wall_before = time.process_time(), time.perf_counter()
    ticks, tick = [], observer.tick

    def timed_tick():
        wall, proc = time.perf_counter(), time.process_time()
        ok = tick()
        ticks.append((time.perf_counter() - wall, time.process_time() - proc))
        return ok
    observer.tick = timed_tick
    stats = observer.run(max_ticks=count)
    stop_synthetic.set()
    if tap:
        tap.stop()
    total_cpu, total_wall = time.process_time() - cpu_before, time.perf_counter() - wall_before
    ids = [r["observation_id"] for r in store.list(limit=1000)]
    rows = collect(store, ids)
    events = event_rows(store) if tap else None
    store.close()
    return {"stub": stub, "input": tap.stats() if tap else None, "events": events, "total_cpu": total_cpu, "total_wall": total_wall,
            "warmup": warmup, "ticks": ticks, "ocr": ocr, "ax": ax, "rows": rows, "stats": stats, "api": getattr(sources._screenshot, "last_path", "stub")}


def event_rows(store):
    """Stored interaction events: counts per type, per observation, and logical bytes per row."""
    db = store.db
    per_type = dict(db.execute("SELECT type, count(*) FROM interaction_events GROUP BY type").fetchall())
    per_obs = [n for (n,) in db.execute("SELECT count(*) FROM interaction_events GROUP BY observation_id")]
    bytes_total = db.execute("SELECT coalesce(sum(length(observation_id) + length(event_id) + 8 + 8 + length(type) "
                             "+ length(payload_json)), 0) FROM interaction_events").fetchone()[0]
    return {"per_type": per_type, "per_obs": per_obs, "rows": sum(per_type.values()), "bytes": bytes_total}


def report_input(m, count, interval, per_day):
    tap, ev = m["input"], m["events"]
    minutes = m["total_wall"] / 60
    print(f"input tap: {tap['state']} {tap['reason'] or ''}  clock resyncs {tap['clock_resyncs']}  tap re-enabled "
          f"{tap['tap_reenabled']}  callback errors {tap['callback_errors']}")
    if tap["state"] == "stopped" or tap["raw"]:
        callbacks = sum(tap["raw"].values())
        print(f"tap callback cpu  total {tap['callback_cpu_ns'] / 1e9:.3f} s over {minutes:.1f} min "
              f"({tap['callback_cpu_ns'] / max(callbacks, 1) / 1000:.1f} us per event, {callbacks} events); "
              f"process cpu total {m['total_cpu']:.1f} s = {m['total_cpu'] / m['total_wall'] * 100:.0f}% of one core")
        for kind in sorted(tap["raw"]):
            print(f"  {kind}: raw {tap['raw'][kind] / minutes:,.0f}/min")
        for kind in sorted(tap["stored"]):
            print(f"  stored {kind}: {tap['stored'][kind] / minutes:,.0f}/min")
        print(f"  cursor: {tap['cursor_raw_moves']} raw moves -> {tap['cursor_samples']} samples "
              f"({tap['cursor_finals']} final)")
    obs = ev["per_obs"]
    if ev["rows"]:
        print(f"event rows: {ev['rows']} in {len(obs)} observations with events (of {count}); per observation "
              f"mean {statistics.mean(obs):.1f} max {max(obs)}; by type {ev['per_type']}")
        per_row = ev["bytes"] / ev["rows"]
        per_minute = ev["rows"] / minutes
        print(f"event bytes/row (logical) {per_row:.0f}; {per_minute:,.0f} rows/min -> "
              f"{per_minute * 60 * 8 * per_row / 1e9:.4f} GB per 8 h at this activity level (continuous use)")


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
    if m["stub"]:
        print("(stub capture: OCR, AX and PNG figures are not real and are not shown)")
        if m["input"]:
            report_input(m, count, interval, per_day)
        print(f"peak RSS    {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6:.0f} MB")
        return
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
    report_ax(rows, m, interval, per_day)
    if m["input"]:
        report_input(m, count, interval, per_day)
    if stored:
        sizes = [r["shot_bytes"] for r in stored]
        print(f"PNG bytes   mean {statistics.mean(sizes):,.0f}  min {min(sizes):,}  max {max(sizes):,}  ->  "
              f"{statistics.mean(sizes) * per_day / 1e9:.2f} GB per 8 h at {interval}s (stored captures only)")
    print(f"peak RSS    {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6:.0f} MB")


def report_ax(rows, m, interval, per_day):
    ax_ok = [r for r in rows if r["ax"] == "succeeded"]
    outcomes = {}
    for r in rows:
        key = r["ax"] + (f"/{r['ax_code']}" if r["ax_code"] else "")
        outcomes[key] = outcomes.get(key, 0) + 1
    print(f"AX outcomes: {outcomes}")
    if not ax_ok:
        return
    print(f"AX latency ms  {ms([r['ax_s'] for r in ax_ok])}")
    cpu, reads = m["ax"].cpu, m["ax"].reads
    print(f"AX cpu s/read  mean {statistics.mean(cpu):.3f}  max {max(cpu):.3f}")
    nodes = [r["nodes"] for r in reads]
    print(f"AX nodes/read  mean {statistics.mean(nodes):,.0f}  min {min(nodes)}  max {max(nodes)}  "
          f"truncated reads: {sum(bool(r['truncated']) for r in reads)}")
    raw = [r["ax_zlib"] for r in ax_ok]
    both = statistics.mean(r["ax_zlib"] + r["ax_chars"] + r["raw_zlib"] + r["chars"] for r in ax_ok)
    print(f"AX raw zlib bytes/capture  mean {statistics.mean(raw):,.0f}  max {max(raw):,}  "
          f"->  {statistics.mean(raw) * per_day / 1e9:.3f} GB per 8 h; OCR + AX structured "
          f"{both * per_day / 1e9:.3f} GB per 8 h")
    apps = {}
    for r in reads:
        apps.setdefault(r["app"], []).append(r)
    for app, items in sorted(apps.items()):
        print(f"  {app}: {len(items)} reads, nodes mean {statistics.mean(i['nodes'] for i in items):,.0f}, "
              f"p50 {percentile([i['ms'] for i in items], .5):.0f} ms, max {max(i['ms'] for i in items):.0f} ms, "
              f"text {statistics.mean(i['chars'] for i in items):,.0f} chars, "
              f"truncated {sum(bool(i['truncated']) for i in items)}")


def running_app(bundle_id):
    import AppKit
    for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
        if app.bundleIdentifier() == bundle_id:
            return app.processIdentifier()
    return None


def measure_apps(bundle_ids, count, interval):
    """Time AccessibilityReader alone on each running app's window (works for apps that are not frontmost)."""
    reader = Measured(AccessibilityReader())
    for bundle_id in bundle_ids:
        pid = running_app(bundle_id)
        if pid is None:
            print(f"{bundle_id}: not running")
            continue
        context = Context({"bundle_id": bundle_id, "name": bundle_id, "pid": pid}, {}, {})
        errors = {}
        for _ in range(count):
            try:
                reader.read(context)
            except Exception as e:                          # noqa: BLE001 - reported per app
                errors[type(e).__name__] = errors.get(type(e).__name__, 0) + 1
            time.sleep(interval)
        mine = [r for r in reader.reads if r["app"] == bundle_id]
        if mine:
            print(f"{bundle_id}: {len(mine)}/{count} ok, nodes mean {statistics.mean(r['nodes'] for r in mine):,.0f} "
                  f"max {max(r['nodes'] for r in mine)}, {ms([r['ms'] / 1000 for r in mine])}, text "
                  f"{statistics.mean(r['chars'] for r in mine):,.0f} chars, truncated "
                  f"{sum(bool(r['truncated']) for r in mine)}, errors {errors or '-'}")
        else:
            print(f"{bundle_id}: no successful read, errors {errors}")
    print(f"cpu s/read  mean {statistics.mean(reader.cpu):.3f}  max {max(reader.cpu):.3f}  "
          f"peak RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6:.0f} MB")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--interval", type=float, default=DEFAULT_CAPTURE_INTERVAL_S)
    parser.add_argument("--ocr-timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--input", action="store_true", help="also run the input event tap")
    parser.add_argument("--synthetic-input", action="store_true", help="--input plus harmless synthetic events")
    parser.add_argument("--stub-capture", action="store_true", help="fake context/screenshot/readers (event path only)")
    parser.add_argument("--ax-apps", help="comma-separated bundle ids: time the AX reader alone on these running apps")
    parser.add_argument("--keep", action="store_true", help="keep the temp directory")
    args = parser.parse_args()
    if args.ax_apps:
        return measure_apps(args.ax_apps.split(","), args.count, min(args.interval, 1.0))
    root = Path(tempfile.mkdtemp(prefix="trace-v2-measure-")).resolve()
    try:
        report(args.count, args.interval, run(args.count, args.interval, root, args.ocr_timeout, args.input,
                                                       args.synthetic_input, args.stub_capture))
    finally:
        if args.keep:
            print(f"kept: {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
