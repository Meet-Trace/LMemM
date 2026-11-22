import argparse
import json
import sys
import time

from .. import paths
from ..observations import ObservationStore
from .processor import build, needed


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m trace_v2.structure")
    parser.add_argument("--data-dir")
    parser.add_argument("--wait-for-ocr", type=float, default=30.0)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        directory = paths.check_data_dir(args.data_dir or paths.data_dir())
    except paths.LegacyDataError as e:
        print(f"refusing: {e}", file=sys.stderr)
        return 2
    store = ObservationStore(directory)
    counts, timing, done = {}, [], 0
    try:
        while args.limit is None or done < args.limit:
            ids = needed(store, time.time_ns(), args.wait_for_ocr, 0, 16)
            if not ids:
                break
            for oid in ids:
                t = time.perf_counter()
                outcome, _ = build(store, oid)
                timing.append((time.perf_counter() - t) * 1000)
                counts[outcome] = counts.get(outcome, 0) + 1
                done += 1
    finally:
        store.close()
    timing.sort()
    print(json.dumps({"processed": done, "outcomes": counts,
                      "ms_p50": round(timing[len(timing) // 2], 1) if timing else None,
                      "ms_max": round(timing[-1], 1) if timing else None}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
