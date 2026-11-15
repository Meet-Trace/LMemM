"""Measure real V2 capture cost: N observations at the capture interval through the real pipeline.

    .venv/bin/python scripts/v2_measure_capture.py [--count 40] [--interval 3.0] [--keep]

Writes only to a fresh temp directory, deleted at the end unless --keep. Never touches V1 data or the real V2 store.
Prints per-capture latency (p50/p95/max), PNG bytes, projected GB per 8 hours, CPU seconds per capture, peak RSS.
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

from trace_v2.macos import MacSources                      # noqa: E402
from trace_v2.observations import ObservationStore         # noqa: E402
from trace_v2.observations.events import EventBuffer       # noqa: E402
from trace_v2.observations.pipeline import observe_once    # noqa: E402
from trace_v2.observations.settings import DEFAULT_CAPTURE_INTERVAL_S  # noqa: E402


def percentile(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p * (len(ordered) - 1)))]


def run(count, interval, root):
    store = ObservationStore(root / "data", legacy_archive=root / "Legacy-V1")
    sources, buffer = MacSources(), EventBuffer(start_ns=time.time_ns())
    latencies, sizes, cpu, withheld, failed = [], [], [], 0, 0
    next_at = time.monotonic()
    for i in range(count):
        wall, proc = time.perf_counter(), time.process_time()
        result = observe_once(store, sources, buffer, f"measure:{i}")
        latencies.append(time.perf_counter() - wall)
        cpu.append(time.process_time() - proc)
        obs = store.get(result.observation_id)
        shot = obs["evidence"]["screenshot"]
        if shot["status"] == "stored":
            sizes.append(len(store.screenshot_bytes(result.observation_id)))
        elif obs["metadata"].get("privacy", {}).get("withheld"):
            withheld += 1
        else:
            failed += 1
        next_at += interval
        time.sleep(max(0.0, next_at - time.monotonic()))
    store.close()
    return latencies, sizes, cpu, withheld, failed, sources


def report(count, interval, latencies, sizes, cpu, withheld, failed, sources):
    ms = [v * 1000 for v in latencies]
    print(f"captures: {count} at {interval}s  stored: {len(sizes)}  withheld: {withheld}  failed: {failed}")
    print(f"screenshot API: {sources._screenshot.last_path}")
    print(f"latency ms  p50 {percentile(ms, .5):.0f}  p95 {percentile(ms, .95):.0f}  max {max(ms):.0f}")
    if sizes:
        mean = statistics.mean(sizes)
        per_day = mean * (8 * 3600 / interval) / 1e9
        print(f"PNG bytes   mean {mean:,.0f}  min {min(sizes):,}  max {max(sizes):,}")
        print(f"projected   {per_day:.2f} GB per 8 h at {interval}s (stored captures only)")
    print(f"cpu s/capture  mean {statistics.mean(cpu):.3f}  max {max(cpu):.3f}")
    print(f"peak RSS    {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6:.0f} MB")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--interval", type=float, default=DEFAULT_CAPTURE_INTERVAL_S)
    parser.add_argument("--keep", action="store_true", help="keep the temp directory")
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="trace-v2-measure-")).resolve()
    try:
        report(args.count, args.interval, *run(args.count, args.interval, root))
    finally:
        if args.keep:
            print(f"kept: {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
