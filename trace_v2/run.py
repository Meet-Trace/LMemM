"""Run the Trace V2 observer:  python -m trace_v2.run [--data-dir DIR] [--interval S] [--duration S] [--once] [--status]

--data-dir   V2 data directory (default: TRACE_V2_DATA_DIR, else ~/Library/Application Support/Trace/V2); a V1 data
             directory is refused.
--interval   capture interval for this run only (default: the stored `capture_interval_s` setting, 3 s)
--duration   stop after this many seconds (default: run until SIGINT/SIGTERM)
--once       take one observation and exit
--status     print the lock holder, the live permission report (preflight only, never prompts), the last runner stats
             and the capture settings, and exit; changes nothing
--ocr-isolation process|inprocess   where Vision runs for this run only (default: the stored `ocr_isolation`, `process`)
--ocr-timeout   OCR timeout in seconds for this run only (default: the stored `ocr_timeout_s`, 10 s)
--stats-every  seconds between runner_stats.json updates (default 300)

Exit codes: 0 clean, 1 the observer thread died, 2 bad arguments or a V1 directory, 3 another runner holds the lock."""

import argparse
import json
import logging
import signal
import sys

from . import paths
from .observations import ObservationStore
from .runner import AlreadyRunning, InstanceLock, Runner, read_status, resolve_interval
from .observations import settings


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m trace_v2.run", description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir")
    parser.add_argument("--interval", type=float)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--ocr-isolation", choices=settings.OCR_ISOLATIONS)
    parser.add_argument("--ocr-timeout", type=float)
    parser.add_argument("--stats-every", type=float, default=None, help="seconds between runner_stats.json updates")
    parser.add_argument("--structure", action="store_true")
    parser.add_argument("--moments", action="store_true")
    parser.add_argument("--activities-model", help="Ollama model that states each Moment's purpose and judges whether two serve one goal; needs --moments")
    return parser


def macos_permissions():
    """The live permission report, or a note when this is not macOS (preflight calls only: nothing prompts)."""
    try:
        from .macos import permissions
    except ImportError:
        return {"error": "permission checks need macOS with pyobjc"}
    return permissions.report()


def stored_settings(directory):
    """The capture-related settings as stored, read without opening the store (nothing is created or migrated)."""
    import sqlite3
    from . import paths
    db_path = paths.database_path(directory)
    if not db_path.exists():
        return {"capture_interval_s": settings.DEFAULT_CAPTURE_INTERVAL_S, "screenshot_scope": settings.DEFAULT_SCREENSHOT_SCOPE,
                "note": "defaults (no database yet)"}
    wanted = (settings.CAPTURE_INTERVAL_KEY, settings.SCREENSHOT_SCOPE_KEY, settings.CURSOR_SAMPLE_KEY, "privacy_policy",
              "screenshot_retention", settings.OCR_ISOLATION_KEY, settings.OCR_TIMEOUT_KEY)
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        found = dict(db.execute(f"SELECT key, value_json FROM settings WHERE key IN ({','.join('?' * len(wanted))})", wanted))
    finally:
        db.close()
    defaults = {settings.CAPTURE_INTERVAL_KEY: settings.DEFAULT_CAPTURE_INTERVAL_S,
                settings.SCREENSHOT_SCOPE_KEY: settings.DEFAULT_SCREENSHOT_SCOPE,
                settings.CURSOR_SAMPLE_KEY: settings.DEFAULT_CURSOR_SAMPLE_MS,
                settings.OCR_ISOLATION_KEY: settings.DEFAULT_OCR_ISOLATION,
                settings.OCR_TIMEOUT_KEY: settings.DEFAULT_OCR_TIMEOUT_S}
    return {k: json.loads(found[k]) if k in found else defaults.get(k) for k in wanted}


def print_status(directory, out=sys.stdout, permissions=macos_permissions):
    status = read_status(directory)
    out.write(f"data dir: {directory}\nrunning: {status['running']}" + (f" (pid {status['pid']})" if status["pid"] else "") + "\n")
    out.write("permissions: " + json.dumps(permissions(), sort_keys=True) + "\n")
    out.write("settings: " + json.dumps(stored_settings(directory), sort_keys=True) + "\n")
    stats = status["stats"]
    out.write("last stats: " + (json.dumps(stats, indent=1, sort_keys=True) if stats else "none yet") + "\n")


def macos_parts(store, ocr=None):
    """The real sources, tap factory, warm-up, permission report, notification pump and front-app probe (macOS only).
    `ocr` holds this run's --ocr-isolation / --ocr-timeout overrides. With a worker process, Vision warms up inside the
    worker and OCR is unavailable until it is ready, so the runner's blocking warm-up is not used."""
    from .macos import InputTap, MacSources, permissions, warmup, windows
    from .macos.lifecycle import Notifications
    from .macos.ocr_client import make_reader
    ocr = ocr or {}
    isolation = ocr.get("isolation") or settings.get_ocr_isolation(store)
    reader = make_reader(isolation, ocr.get("timeout_s") or settings.get_ocr_timeout(store),
                         settings.get_ocr_worker_max_rss_mb(store), settings.get_ocr_worker_max_jobs(store))

    def tap_factory(buffer, cursor_ms, gate):
        return InputTap(buffer, cursor_sample_ms=cursor_ms, gate=gate)
    return (MacSources(ocr=reader, screenshot_scope=settings.get_screenshot_scope(store)), tap_factory,
            warmup.warm_up if isolation == "inprocess" else None, permissions.report, Notifications, lambda: windows.frontmost_application()["bundle_id"])


def main(argv=None, parts=macos_parts, permissions=macos_permissions):
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        directory = paths.check_data_dir(args.data_dir or paths.data_dir())
    except paths.LegacyDataError as e:
        print(f"refusing: {e}", file=sys.stderr)
        return 2
    if args.status:
        print_status(directory, permissions=permissions)
        return 0
    try:
        lock = InstanceLock(directory).acquire()
    except AlreadyRunning as e:
        print(str(e), file=sys.stderr)
        return 3
    store = ObservationStore(directory)
    notifications, previous, sources = None, {}, None
    try:
        interval = resolve_interval(store, args.interval)
        overrides = {k: v for k, v in (("isolation", args.ocr_isolation), ("timeout_s", args.ocr_timeout)) if v is not None}
        if args.ocr_timeout is not None:
            settings._valid_ocr_timeout(args.ocr_timeout)
        sources, tap_factory, warmup, report, notification_class, *more = parts(store, **({"ocr": overrides} if overrides else {}))
        runner = Runner(store, sources, interval, tap_factory=tap_factory,
                        cursor_sample_ms=settings.get_cursor_sample_ms(store), warmup=warmup, ocr_mode=settings.get_ocr_mode(store),
                        permissions=report, front_app=more[0] if more else None, structure=args.structure, moments=args.moments, activities_model=args.activities_model, **({} if args.stats_every is None else {"stats_every_s": args.stats_every}))
        notifications = notification_class(runner.notify)
        runner.pump = notifications.pump
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda *_: runner.request_stop())
        runner.run(duration_s=args.duration, once=args.once)
        return 1 if runner.observer_error else 0
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        close = getattr(getattr(sources, "ocr", None), "close", None)
        if close is not None:
            close()                                  # stops the OCR worker process (it also exits if we are killed)
        if notifications is not None:
            notifications.close()
        store.close()
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
