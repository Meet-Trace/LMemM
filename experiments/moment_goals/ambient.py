import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

STATUS = "ambient_status.json"
STATE = "ambient_state.json"


def read_json(path, default):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def write_json(path, value):
    temporary = path + ".tmp"
    with open(temporary, "w") as handle:
        json.dump(value, handle, indent=1)
    os.replace(temporary, path)


def signature(moments):
    digest = hashlib.sha256()
    for m in moments:
        digest.update(f"{m['id']}|{m.get('revision')}|{m.get('state')}|{hashlib.sha256(m['prompt'].encode()).hexdigest()[:12]};".encode())
    return digest.hexdigest()[:16]


def pause_reason(min_battery, min_free_pct):
    import run_vision
    battery = run_vision.parse_battery(run_vision.run(["pmset", "-g", "batt"]))
    if battery["source"] == "battery" and battery["percent"] is not None and battery["percent"] < min_battery:
        return f"battery at {battery['percent']}% (below {min_battery}%)"
    free = run_vision.parse_memory_free(run_vision.run(["memory_pressure"]))
    if free is not None and free < min_free_pct:
        return f"only {free}% memory free (below {min_free_pct}%)"
    return None


def ollama_down(host):
    import urllib.request
    try:
        urllib.request.urlopen(host + "/api/tags", timeout=5).read()
    except Exception as exc:
        return f"Ollama is not reachable at {host} ({type(exc).__name__}); open the Ollama app"
    return None


def describe_counts(goals_dir, model):
    result = read_json(os.path.join(goals_dir, "moment_goals.json"), {})
    moments = result.get("moments", [])
    entries = {g["moment_id"]: g for g in result.get("models", {}).get(model, {}).get("goals", [])}
    purpose = {g["moment_id"]: g for g in result.get("models", {}).get(f"{model} (purpose)", {}).get("goals", [])}
    described = sum(1 for m in moments if entries.get(m["id"], {}).get("goal"))
    groups = result.get("models", {}).get(f"{model} (purpose)", {}).get("groups", [])
    return {"moments": len(moments), "described": described, "purposes": len(purpose),
            "activities": sum(1 for g in groups if len(g) > 1)}, moments


class Observer:
    def __init__(self, directory, goals_dir):
        self.directory = directory
        self.process = None
        self.restarts = 0
        self.gave_up = None
        self.log = open(os.path.join(goals_dir, "observer.log"), "ab")
        self.next_try = 0.0

    def ensure(self):
        if self.gave_up:
            return
        if self.process is not None and self.process.poll() is None:
            return
        if self.process is not None:
            code = self.process.returncode
            self.process = None
            if code == 3:
                self.gave_up = "another observer already holds the lock, so this one is not started"
                return
            self.restarts += 1
        if time.time() < self.next_try:
            return
        self.next_try = time.time() + 10
        self.process = subprocess.Popen([sys.executable, "-m", "trace_v2.run", "--data-dir", self.directory, "--moments"],
                                        cwd=REPO, stdout=self.log, stderr=subprocess.STDOUT)

    def state(self):
        if self.gave_up:
            return {"running": "elsewhere", "note": self.gave_up, "restarts": self.restarts}
        running = self.process is not None and self.process.poll() is None
        return {"running": running, "pid": self.process.pid if running else None, "restarts": self.restarts}

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(30)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.log.close()


def interpret(args, goals_dir):
    import run
    import run_vision
    import purpose
    run.main(["--data-dir", args.data_dir, "--out", goals_dir, "--no-model", "--keep", "--stable-only", "--host", args.host])
    counts, moments = describe_counts(goals_dir, args.model)
    state = read_json(os.path.join(goals_dir, STATE), {})
    current = signature(moments)
    if current == state.get("signature") or not moments:
        return counts, False
    reason = pause_reason(args.min_battery, args.min_free_pct) or ollama_down(args.host)
    if reason:
        return counts, reason
    run_vision.main(["--data-dir", args.data_dir, "--goals", goals_dir, "--host", args.host, "--model", args.model, "--only-new"])
    purpose.main(["--goals", goals_dir, "--model", args.model, "--host", args.host])
    state["signature"] = signature(read_json(os.path.join(goals_dir, "moment_goals.json"), {}).get("moments", []))
    write_json(os.path.join(goals_dir, STATE), state)
    counts, _ = describe_counts(goals_dir, args.model)
    return counts, True


def show_status(args, goals_dir):
    status = read_json(os.path.join(goals_dir, STATUS), None)
    if status is None:
        print("ambient has not run yet: no status file in " + goals_dir)
        return 1
    age = time.time() - status.get("updated", 0)
    print(f"last update {age:.0f} s ago ({'alive' if age < 3 * args.interval + 120 else 'STALE, the process looks stopped'})")
    print(f"observer: {json.dumps(status.get('observer'))}")
    for k in ("moments", "described", "purposes", "activities", "last_described_at", "paused", "last_error", "cycle_s", "cycles"):
        print(f"{k}: {status.get(k)}")
    try:
        from trace_v2.runner import read_status
        stats = read_status(args.data_dir)
        print(f"capture runner holds lock: {stats['running']}")
        if stats["stats"]:
            print("capture stats: " + json.dumps(stats["stats"], sort_keys=True)[:600])
    except Exception as exc:
        print(f"capture stats unavailable: {exc}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ambient.py")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--goals", required=True)
    parser.add_argument("--model", default="lfm3-vl:latest")
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--min-battery", type=int, default=20)
    parser.add_argument("--min-free-pct", type=int, default=10)
    parser.add_argument("--no-capture", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    args.data_dir = os.path.abspath(os.path.expanduser(args.data_dir))
    goals_dir = os.path.abspath(os.path.expanduser(args.goals))
    os.makedirs(goals_dir, exist_ok=True)
    if args.status:
        return show_status(args, goals_dir)
    stop = {"now": False}
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.update(now=True))
    observer = None if args.no_capture else Observer(args.data_dir, goals_dir)
    status = read_json(os.path.join(goals_dir, STATUS), {})
    status.update({"started": time.time(), "cycles": 0, "model": args.model})
    print(f"ambient started: capture {'off' if observer is None else 'on'}, model {args.model}, cycle {args.interval:.0f} s. Ctrl-C to stop.", flush=True)
    try:
        while not stop["now"]:
            began = time.time()
            if observer:
                observer.ensure()
            error, paused, changed = None, None, False
            try:
                counts, outcome = interpret(args, goals_dir)
                if isinstance(outcome, str):
                    paused = outcome
                changed = outcome is True
                status.update(counts)
                if changed:
                    status["last_described_at"] = time.time()
            except SystemExit as exc:
                error = f"stopped: {exc}"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            if error:
                status.update(describe_counts(goals_dir, args.model)[0])
            status.update({"updated": time.time(), "cycles": status["cycles"] + 1, "cycle_s": round(time.time() - began, 1),
                           "observer": observer.state() if observer else {"running": "not managed"}, "paused": paused, "last_error": error})
            write_json(os.path.join(goals_dir, STATUS), status)
            print(f"[{time.strftime('%H:%M:%S')}] moments {status.get('moments')} described {status.get('described')} "
                  f"activities {status.get('activities')} observer {status['observer'].get('running')}"
                  + (f" PAUSED: {paused}" if paused else "") + (f" ERROR: {error}" if error else ""), flush=True)
            if args.once:
                break
            waited = 0.0
            while waited < args.interval and not stop["now"]:
                if observer:
                    observer.ensure()
                time.sleep(1)
                waited += 1
    finally:
        if observer:
            observer.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
