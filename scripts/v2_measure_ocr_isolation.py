"""Measure the real V2 runner with Vision OCR in process versus in a worker process, and a forced OCR stall.

    .venv/bin/python scripts/v2_measure_ocr_isolation.py run --isolation process --seconds 180 [--interval 3]
    .venv/bin/python scripts/v2_measure_ocr_isolation.py stall --stalls 3 --timeout 10 [--seconds 120]

`run` starts `python -m trace_v2.run` (front_app scope, a temp data dir deleted afterwards) and samples the runner and
its OCR worker with ps every 2 s: CPU seconds and RSS of both processes. Capture latency per observation is the time
from the capture start to the later of the OCR and accessibility reader finishing, read back from the store.
`stall` runs the same observer loop in this process with an OCR worker that hangs on its first N jobs
(scripts/v2_ocr_stalling_worker.py, test only) and reports missed ticks and recovery. Prints JSON.
"""

import argparse
import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def pct(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p * (len(ordered) - 1)))] if ordered else None


def cpu_seconds(text):
    """ps `time=`: [[H:]M:]S.cc"""
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + float(part)
    return total


def snapshot(root_pid):
    out = subprocess.run(["ps", "-ax", "-o", "pid=,ppid=,rss=,time=,command="], capture_output=True, text=True).stdout
    rows = {}
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) == 5:
            rows[int(parts[0])] = (int(parts[1]), int(parts[2]) / 1024, cpu_seconds(parts[3]), parts[4])
    main = rows.get(root_pid)
    workers = {pid: r for pid, r in rows.items() if r[0] == root_pid and "ocr_worker" in r[3]}
    return main, workers


class Sampler(threading.Thread):
    def __init__(self, pid, every=2.0):
        super().__init__(daemon=True)
        self.pid, self.every, self.stop_event = pid, every, threading.Event()
        self.samples, self.worker_cpu = [], {}

    def run(self):
        while not self.stop_event.is_set():
            main, workers = snapshot(self.pid)
            if main:
                for pid, row in workers.items():
                    self.worker_cpu[pid] = max(self.worker_cpu.get(pid, 0.0), row[2])
                self.samples.append({"main_rss": main[1], "main_cpu": main[2],
                                     "worker_rss": sum(r[1] for r in workers.values()), "workers": len(workers)})
            self.stop_event.wait(self.every)


def read_store(data):
    db = sqlite3.connect(f"file:{data}/observations.sqlite3?mode=ro", uri=True) if (Path(data) / "observations.sqlite3").exists() \
        else sqlite3.connect(next(Path(data).glob("*.sqlite*")))
    rows = db.execute("SELECT o.captured_at_ns, json_extract(o.privacy_json,'$.withheld'), "
                      "(SELECT status||'/'||coalesce(error_code,'') FROM extractions WHERE observation_id=o.observation_id AND reader='ocr' AND revision=1), "
                      "(SELECT started_at_ns FROM extractions WHERE observation_id=o.observation_id AND reader='ocr' AND revision=1), "
                      "(SELECT finished_at_ns FROM extractions WHERE observation_id=o.observation_id AND reader='ocr' AND revision=1), "
                      "(SELECT finished_at_ns FROM extractions WHERE observation_id=o.observation_id AND reader='accessibility' AND revision=1) "
                      "FROM observations o ORDER BY o.captured_at_ns").fetchall()
    db.close()
    return rows


def summarize(rows, stats, sampler, wall_s):
    attempted = [r for r in rows if not r[1] and r[2] and not r[2].startswith("skipped")]
    outcomes = {}
    for r in rows:
        outcomes[r[2]] = outcomes.get(r[2], 0) + 1
    latency = [(max(x for x in (r[4], r[5]) if x) - r[0]) / 1e6 for r in rows if r[4] and r[5] and not r[1]]
    ocr_ms = [(r[4] - r[3]) / 1e6 for r in rows if r[2] and r[2].startswith("succeeded") and r[3] and r[4]]
    main_cpu = max((s["main_cpu"] for s in sampler.samples), default=0.0)
    worker_cpu = sum(sampler.worker_cpu.values())
    n = max(len(rows), 1)
    total_rss = [s["main_rss"] + s["worker_rss"] for s in sampler.samples]
    ok = sum(v for k, v in outcomes.items() if k and k.startswith("succeeded"))
    return {"observations": len(rows), "ocr_attempted": len(attempted), "ocr_outcomes": outcomes,
            "ocr_success_rate": round(ok / max(len(attempted), 1), 3),
            "capture_ms": {"p50": round(pct(latency, .5) or 0), "p95": round(pct(latency, .95) or 0), "max": round(max(latency, default=0))},
            "ocr_ms": {"p50": round(pct(ocr_ms, .5) or 0), "p95": round(pct(ocr_ms, .95) or 0), "max": round(max(ocr_ms, default=0))},
            "missed_ticks": stats.get("missed_ticks"), "failures": stats.get("failures"),
            "cpu_s": {"main": round(main_cpu, 1), "worker": round(worker_cpu, 1), "total": round(main_cpu + worker_cpu, 1)},
            "cpu_pct_of_one_core": {"main": round(main_cpu / wall_s * 100, 1), "total": round((main_cpu + worker_cpu) / wall_s * 100, 1)},
            "cpu_s_per_capture": {"main": round(main_cpu / n, 3), "total": round((main_cpu + worker_cpu) / n, 3)},
            "rss_mb": {"main_median": round(statistics.median(s["main_rss"] for s in sampler.samples), 0),
                       "main_peak": round(max(s["main_rss"] for s in sampler.samples), 0),
                       "worker_median": round(statistics.median(s["worker_rss"] for s in sampler.samples), 0),
                       "worker_peak": round(max(s["worker_rss"] for s in sampler.samples), 0),
                       "total_median": round(statistics.median(total_rss), 0), "total_peak": round(max(total_rss), 0)},
            "ocr_worker_stats": stats.get("ocr_worker")}


def run_mode(args):
    data = Path(tempfile.mkdtemp(prefix="trace-v2-ocrmeasure-")).resolve()
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    load_before = os.getloadavg()
    try:
        proc = subprocess.Popen([sys.executable, "-m", "trace_v2.run", "--data-dir", str(data), "--duration", str(args.seconds),
                                 "--interval", str(args.interval), "--ocr-isolation", args.isolation, "--stats-every", "10"],
                                cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        sampler = Sampler(proc.pid)
        started = time.monotonic()
        sampler.start()
        _, err = proc.communicate(timeout=args.seconds + 240)
        wall = time.monotonic() - started
        sampler.stop_event.set()
        sampler.join(5)
        stats = json.loads((data / "runner_stats.json").read_text())
        result = summarize(read_store(data), stats, sampler, wall)
        result.update({"isolation": args.isolation, "returncode": proc.returncode, "wall_s": round(wall, 1),
                       "load_avg_before": [round(x, 2) for x in load_before], "load_avg_after": [round(x, 2) for x in os.getloadavg()],
                       "scope": "front_app", "interval_s": args.interval})
        print(json.dumps(result, indent=1))
    finally:
        shutil.rmtree(data, ignore_errors=True)


class DemoContext:
    """An ungated app, so the demo exercises OCR whatever is in front on the real screen (and no real screen content is read)."""

    def context(self):
        from trace_v2.observations import Context
        return Context({"bundle_id": "com.example.demo", "name": "Demo", "pid": 1}, {"title": "demo", "id": 1},
                       {"id": 1, "bounds": [0, 0, 1440, 900], "scale": 1.0})

    def secure_input(self):
        return False


class DemoScreen:
    """A rendered page of text (tests/v2_macos_render.py), a new capture time each call."""

    def __init__(self):
        sys.path.insert(0, str(REPO / "tests"))
        from v2_macos_render import render_png
        self.png = render_png(["Quarterly planning review", "Invoice 20481 was approved on Tuesday",
                               "The quick brown fox jumps over the lazy dog"] * 3, 1440, 900, font_size=30.0)

    def screenshot(self, display_id, **_scope):
        from trace_v2.observations import ScreenshotCapture
        return ScreenshotCapture(self.png, "png", 1440, 900, time.time_ns())


def stall_mode(args):
    from trace_v2.macos import MacSources
    from trace_v2.macos.ocr_client import OcrWorkerClient
    from trace_v2.observations import ObservationStore
    from trace_v2.observations.events import EventBuffer
    from trace_v2.observations.pipeline import ContinuousObserver
    root = Path(tempfile.mkdtemp(prefix="trace-v2-stall-")).resolve()
    counter = root / "stalls"
    env = {**os.environ, "STALL_COUNT_FILE": str(counter), "STALL_JOBS": str(args.stalls)}
    try:
        client = OcrWorkerClient(args.timeout, env=env,
                                 command=[sys.executable, str(REPO / "scripts" / "v2_ocr_stalling_worker.py")])
        if not client.wait_ready(120):
            raise SystemExit("worker never became ready")
        store = ObservationStore(root / "data", legacy_archive=root / "Legacy-V1")
        sources = MacSources(context_source=DemoContext(), screenshot_source=DemoScreen(), ocr=client)
        observer = ContinuousObserver(store, sources, EventBuffer(start_ns=time.time_ns()), interval_s=args.interval)
        stop, load = threading.Event(), os.getloadavg()
        threading.Timer(args.seconds, stop.set).start()
        stats = observer.run(stop)
        rows = read_store(root / "data")
        client_stats = client.stats()
        client.close()
        outcomes = {}
        for r in rows:
            outcomes[r[2]] = outcomes.get(r[2], 0) + 1
        gaps = [(b[0] - a[0]) / 1e9 for a, b in zip(rows, rows[1:])]
        print(json.dumps({"stalls_forced": args.stalls, "timeout_s": args.timeout, "interval_s": args.interval,
                          "seconds": args.seconds, "observations": len(rows), "missed_ticks": stats["missed_ticks"],
                          "failures": stats["failures"], "ocr_outcomes": outcomes,
                          "max_gap_between_captures_s": round(max(gaps, default=0), 2),
                          "gaps_over_1.5x_interval": sum(g > 1.5 * args.interval for g in gaps),
                          "ocr_worker": {k: client_stats[k] for k in ("jobs", "succeeded", "timeouts", "crashes", "spawns", "restarts",
                                                                    "not_ready_skipped", "busy_refused", "late_results_discarded")},
                          "load_avg": [round(x, 2) for x in load]}, indent=1))
        store.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("run")
    r.add_argument("--isolation", choices=("process", "inprocess"), required=True)
    r.add_argument("--seconds", type=float, default=180)
    r.add_argument("--interval", type=float, default=3.0)
    s = sub.add_parser("stall")
    s.add_argument("--stalls", type=int, default=3)
    s.add_argument("--timeout", type=float, default=10.0)
    s.add_argument("--seconds", type=float, default=120)
    s.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args()
    run_mode(args) if args.mode == "run" else stall_mode(args)


if __name__ == "__main__":
    main()
