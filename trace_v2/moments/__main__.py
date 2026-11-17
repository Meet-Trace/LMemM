import argparse
import json
import sys
import time
from datetime import datetime

from .. import paths
from ..observations import ObservationStore
from ..observations.queries import _decode
from ..structure.processor import read_connection
from .builder import ALGORITHM, run_pass
from .similarity import PARAMS


def current_moments(conn):
    rows = conn.execute(
        "SELECT r.moment_id, r.revision, r.state, r.start_ns, r.end_ns, r.observation_count, r.summary_json, m.origin "
        "FROM moment_revisions r JOIN moments m USING (moment_id) WHERE m.algorithm=? AND r.revision=("
        "SELECT max(x.revision) FROM moment_revisions x WHERE x.moment_id=r.moment_id) ORDER BY r.start_ns, r.moment_id",
        (ALGORITHM,))
    return rows.fetchall()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m trace_v2.moments")
    parser.add_argument("--data-dir")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--stabilise-s", type=float)
    args = parser.parse_args(argv)
    try:
        directory = paths.check_data_dir(args.data_dir or paths.data_dir())
    except paths.LegacyDataError as e:
        print(f"refusing: {e}", file=sys.stderr)
        return 2
    params = dict(PARAMS)
    if args.stabilise_s is not None:
        params["stabilise_s"] = args.stabilise_s
    store = ObservationStore(directory)
    reader = read_connection(store)
    totals = {}
    try:
        if not args.list:
            while True:
                out = run_pass(store, reader, time.time_ns(), params)
                for k, v in out.items():
                    totals[k] = totals.get(k, 0) + v
                if out.get("processed", 0) + out.get("skipped", 0) < 300 or out.get("stale"):
                    break
        rows = current_moments(reader)
        fmt = lambda ns: datetime.fromtimestamp(ns / 1e9).strftime("%m-%d %H:%M:%S")
        for mid, rev, state, start, end, count, summary, origin in rows:
            s = json.loads(summary)
            apps = ",".join(f"{k.split('.')[-1]}:{v}" for k, v in list(s["apps"].items())[:3])
            print(f"{mid[:8]} r{rev:<2} {state:<12} {fmt(start)}-{fmt(end)[6:]} n={count:<4} {apps} | {' '.join(s['top_tokens'][:6])}")
        print(json.dumps({"passes": totals, "moments": len(rows)}))
    finally:
        reader.close()
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
