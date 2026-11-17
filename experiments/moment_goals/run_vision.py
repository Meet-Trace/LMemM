import argparse
import base64
import hashlib
import io
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from inference import parse_output

NOTE = ("The attached screenshots show observations {tags} of the sequence below, in that order. "
        "Use them as visual evidence for those observations only.\n\n")


def call(host, path, body, timeout=900):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(host + path, data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def pick_model(host, wanted):
    if wanted:
        return wanted
    names = [m["name"] for m in call(host, "/api/tags", None, 15).get("models", [])]
    for name in names:
        if "lfm" in name.lower():
            return name
    raise SystemExit("no LFM model found in ollama; installed: " + ", ".join(names))


def tag_order(tags):
    return sorted(tags, key=lambda t: int(t[1:]))


def representative(ids):
    return ids[len(ids) // 2]


def choose_tags(tags, limit):
    order = tag_order(tags)
    if limit <= 0 or len(order) <= limit:
        return order
    if limit == 1:
        return [order[len(order) // 2]]
    picks = sorted({round(i * (len(order) - 1) / (limit - 1)) for i in range(limit)})
    return [order[i] for i in picks]


def screenshot_path(conn, root, oid):
    row = conn.execute("SELECT status, relative_path FROM screenshots WHERE observation_id=?", (oid,)).fetchone()
    if not row or row[0] != "stored" or not row[1]:
        return None
    path = os.path.join(root, row[1])
    return path if os.path.exists(path) else None


def encode_image(path, side):
    from PIL import Image
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((side, side))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=80)
    data = buffer.getvalue()
    return base64.b64encode(data).decode(), len(data)


def build_message(prompt, tags_used, images):
    text = NOTE.format(tags=", ".join(tags_used)) + prompt if tags_used else prompt
    return {"role": "user", "content": text, "images": images}


def parse_ps(text, needle="ollama"):
    rss = cpu = 0.0
    count = 0
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or needle not in parts[2].lower():
            continue
        try:
            rss += float(parts[0]) / 1024
            cpu += float(parts[1])
            count += 1
        except ValueError:
            continue
    return {"rss_mb": rss, "cpu_pct": cpu, "processes": count}


def parse_system_cpu(text):
    total = 0.0
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2:
            try:
                total += float(parts[1])
            except ValueError:
                continue
    return total / (os.cpu_count() or 1)


def parse_battery(text):
    percent = re.search(r"(\d+)%", text)
    source = "ac" if "AC Power" in text else "battery" if "Battery Power" in text else None
    return {"percent": int(percent.group(1)) if percent else None, "source": source}


def parse_swap(text):
    used = re.search(r"used = ([\d.]+)M", text)
    return float(used.group(1)) if used else None


def parse_therm(text):
    limit = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", text)
    return int(limit.group(1)) if limit else None


def parse_memory_free(text):
    free = re.search(r"free percentage:\s*(\d+)%", text)
    return int(free.group(1)) if free else None


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def snapshot():
    return {"battery": parse_battery(run(["pmset", "-g", "batt"])), "thermal_cpu_limit": parse_therm(run(["pmset", "-g", "therm"])),
            "swap_used_mb": parse_swap(run(["sysctl", "-n", "vm.swapusage"])), "memory_free_pct": parse_memory_free(run(["memory_pressure"])),
            "load_avg": os.getloadavg()[0]}


def probe_ms():
    block = b"x" * 1_000_000
    started = time.perf_counter()
    for _ in range(12):
        hashlib.sha256(block).digest()
    return (time.perf_counter() - started) * 1000


class Sampler:
    def __init__(self, interval=1.0):
        self.interval = interval
        self.rss, self.cpu, self.system, self.probes = [], [], [], []
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            listing = run(["ps", "-axo", "rss=,pcpu=,comm="])
            sample = parse_ps(listing)
            self.rss.append(sample["rss_mb"])
            self.cpu.append(sample["cpu_pct"])
            self.system.append(parse_system_cpu(listing))
            self.probes.append(probe_ms())
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(5)
        return {"rss_peak_mb": round(max(self.rss, default=0), 1), "ollama_cpu_avg_pct": round(statistics.fmean(self.cpu), 1) if self.cpu else 0.0,
                "ollama_cpu_peak_pct": round(max(self.cpu, default=0), 1), "system_cpu_avg_pct": round(statistics.fmean(self.system), 1) if self.system else 0.0,
                "probe_ms": round(statistics.median(self.probes), 2) if self.probes else None,
                "probe_ms_max": round(max(self.probes), 2) if self.probes else None, "samples": len(self.rss)}


def ps_models(host):
    try:
        return [{"model": m.get("name"), "size_mb": round((m.get("size") or 0) / 1e6), "vram_mb": round((m.get("size_vram") or 0) / 1e6)}
                for m in call(host, "/api/ps", None, 10).get("models", [])]
    except Exception:
        return []


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def summarize(entries, baseline_probe, start, end, models_loaded):
    seconds = [e["seconds"] for e in entries if e.get("seconds") is not None]
    ok = [e for e in entries if e.get("metrics")]
    gen = [e["metrics"]["gen_tokens_per_s"] for e in ok if e["metrics"].get("gen_tokens_per_s")]
    probes = [e["metrics"]["probe_ms"] for e in ok if e["metrics"].get("probe_ms")]
    slow = (statistics.median(probes) / baseline_probe) if probes and baseline_probe else None
    battery = (start["battery"]["percent"], end["battery"]["percent"])
    return {
        "calls": len(entries), "seconds_mean": round(statistics.fmean(seconds), 2) if seconds else None,
        "seconds_median": round(statistics.median(seconds), 2) if seconds else None, "seconds_p95": percentile(seconds, 0.95),
        "seconds_max": max(seconds, default=None), "first_call_seconds": seconds[0] if seconds else None,
        "load_seconds_max": max((e["metrics"].get("load_s", 0) for e in ok), default=None),
        "prompt_tokens_avg": round(statistics.fmean(e["prompt_eval_count"] for e in ok if e.get("prompt_eval_count"))) if any(e.get("prompt_eval_count") for e in ok) else None,
        "output_tokens_avg": round(statistics.fmean(e["eval_count"] for e in ok if e.get("eval_count"))) if any(e.get("eval_count") for e in ok) else None,
        "gen_tokens_per_s_avg": round(statistics.fmean(gen), 1) if gen else None,
        "ollama_rss_peak_mb": max((e["metrics"]["rss_peak_mb"] for e in ok), default=None),
        "ollama_cpu_avg_pct": round(statistics.fmean(e["metrics"]["ollama_cpu_avg_pct"] for e in ok), 1) if ok else None,
        "system_cpu_avg_pct": round(statistics.fmean(e["metrics"]["system_cpu_avg_pct"] for e in ok), 1) if ok else None,
        "baseline_probe_ms": baseline_probe, "probe_during_ms_median": round(statistics.median(probes), 2) if probes else None,
        "slowdown_factor": round(slow, 2) if slow else None,
        "battery_percent_start_end": battery, "power_source": start["battery"]["source"],
        "thermal_cpu_limit_start_end": (start["thermal_cpu_limit"], end["thermal_cpu_limit"]),
        "swap_used_mb_start_end": (start["swap_used_mb"], end["swap_used_mb"]),
        "memory_free_pct_start_end": (start["memory_free_pct"], end["memory_free_pct"]),
        "models_loaded_after": models_loaded, "ran_on_gpu": any(m["vram_mb"] > 0 for m in models_loaded),
        "parsed_ok": sum(1 for e in entries if e.get("parsed", {}).get("status") == "ok"),
        "insufficient": sum(1 for e in entries if e.get("parsed", {}).get("status") == "insufficient"),
        "invalid": sum(1 for e in entries if e.get("parsed", {}).get("status") == "invalid"),
    }


SCHEMA = {
    "type": "object",
    "properties": {
        "moment_description": {"type": ["string", "null"]},
        "evidence": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
            "type": {"type": "string", "enum": ["application_context", "content", "interaction", "temporal"]},
            "description": {"type": "string"},
            "observations": {"type": "array", "maxItems": 4, "items": {"type": "string", "pattern": "^O[0-9]+$"}}},
            "required": ["type", "description", "observations"]}},
        "confidence": {"type": "number"},
        "alternative_interpretation": {"type": ["string", "null"]},
        "insufficient_evidence": {"type": "boolean"}},
    "required": ["moment_description", "evidence", "confidence", "alternative_interpretation", "insufficient_evidence"]}


def chat(host, model, message, args):
    options = {"num_ctx": args.num_ctx, "num_predict": args.num_predict}
    if args.temperature is not None:
        options["temperature"] = args.temperature
    if args.repeat_penalty is not None:
        options["repeat_penalty"] = args.repeat_penalty
    if args.threads:
        options["num_thread"] = args.threads
    if args.cpu:
        options["num_gpu"] = 0
    body = {"model": model, "messages": [message], "stream": False, "keep_alive": "10m", "options": options}
    if args.format == "json":
        body["format"] = "json"
    elif args.format == "schema":
        body["format"] = SCHEMA
    if args.think != "default":
        body["think"] = args.think == "on"
    try:
        return call(host, "/api/chat", body, args.timeout)
    except urllib.error.HTTPError as exc:
        if "think" in body and exc.code == 400:
            body.pop("think")
            return call(host, "/api/chat", body, args.timeout)
        raise


def metrics_of(reply, sampled, seconds):
    ns = 1e9
    eval_s = (reply.get("eval_duration") or 0) / ns
    return {**sampled, "load_s": round((reply.get("load_duration") or 0) / ns, 2),
            "prompt_eval_s": round((reply.get("prompt_eval_duration") or 0) / ns, 2), "eval_s": round(eval_s, 2),
            "gen_tokens_per_s": round(reply["eval_count"] / eval_s, 1) if reply.get("eval_count") and eval_s else None}


def one_moment(conn, root, host, model, moment, args):
    tags = moment.get("tags") or {}
    used = choose_tags(tags, args.max_images)
    images, ids, sizes, kept = [], [], [], []
    for tag in used:
        oid = representative(tags[tag])
        path = screenshot_path(conn, root, oid)
        if not path:
            continue
        data, size = encode_image(path, args.image_side)
        images.append(data)
        ids.append(oid)
        sizes.append(size)
        kept.append(tag)
    if not images:
        return {"goal": "", "keywords": [], "parsed": {"status": "invalid", "description": None, "evidence": [], "confidence": 0.0,
                "alternative": None, "insufficient_evidence": False, "dropped_evidence": 0, "errors": ["no stored screenshots for this moment"],
                "observation_ids": []}, "raw": "", "seconds": None, "images": [], "prompt_sent": moment["prompt"]}
    message = build_message(moment["prompt"], kept, images)
    sampler = Sampler()
    sampler.start()
    started = time.perf_counter()
    try:
        reply = chat(host, model, message, args)
        error = None
    except Exception as exc:
        reply, error = {}, str(exc)
    seconds = round(time.perf_counter() - started, 2)
    sampled = sampler.stop()
    content = (reply.get("message") or {}).get("content", "") or ""
    thinking = (reply.get("message") or {}).get("thinking", "") or ""
    parsed = parse_output(content, tags)
    if not content.strip() and thinking.strip():
        parsed["errors"].append("the model produced only reasoning text and no answer")
    if error:
        parsed["errors"].append("request failed: " + error)
    return {"goal": parsed["description"] or "", "keywords": [], "parsed": parsed, "raw": content or (("[reasoning only] " + thinking) if thinking else (error or "")), "thinking": thinking,
            "seconds": seconds, "eval_count": reply.get("eval_count"), "prompt_eval_count": reply.get("prompt_eval_count"),
            "metrics": {**metrics_of(reply, sampled, seconds), "image_count": len(images), "image_kb": round(sum(sizes) / 1024)},
            "images": ids, "image_tags": kept, "prompt_sent": message["content"]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--goals", required=True)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-images", type=int, default=4)
    parser.add_argument("--image-side", type=int, default=896)
    parser.add_argument("--num-ctx", type=int, default=6144)
    parser.add_argument("--num-predict", type=int, default=450)
    parser.add_argument("--threads", type=int, default=0)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--think", choices=("default", "off", "on"), default="off")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--format", choices=("none", "json", "schema"), default="none")
    parser.add_argument("--repeat-penalty", type=float, default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args(argv)
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
    from trace_v2 import paths
    from trace_v2.observations import ObservationStore
    from trace_v2.structure.processor import read_connection
    directory = paths.check_data_dir(os.path.abspath(os.path.expanduser(args.data_dir)))
    goals_dir = os.path.abspath(os.path.expanduser(args.goals))
    result_path = os.path.join(goals_dir, "moment_goals.json")
    result = json.load(open(result_path))
    model = pick_model(args.host, args.model)
    cache_path = os.path.join(goals_dir, "vision_cache.json")
    cache = {} if args.fresh or not os.path.exists(cache_path) else json.load(open(cache_path))
    store = ObservationStore(directory)
    conn = read_connection(store)
    moments = result["moments"][:args.limit] if args.limit else result["moments"]
    baseline = [probe_ms() for _ in range(7)]
    baseline_probe = round(statistics.median(baseline), 2)
    start = snapshot()
    entries = []
    for index, moment in enumerate(moments):
        key = hashlib.sha256(f"{model}|{moment['id']}|{moment['prompt']}|{args.max_images}|{args.image_side}|{args.cpu}|{args.format}|{args.think}|{args.repeat_penalty}|{args.temperature}|{args.threads}".encode()).hexdigest()[:20]
        if key not in cache:
            cache[key] = one_moment(conn, str(paths.assets_dir(directory)), args.host, model, moment, args)
            json.dump(cache, open(cache_path, "w"))
        entry = cache[key]
        entries.append(entry)
        print(f"  M{index:<3} {entry['parsed']['status'][:4]} {entry.get('seconds')} s  {entry['goal'][:100]}", flush=True)
        if entry["parsed"]["status"] == "invalid":
            print("       raw reply starts: " + repr(entry["raw"][:300]), flush=True)
    end = snapshot()
    loaded = ps_models(args.host)
    summary = summarize(entries, baseline_probe, start, end, loaded)
    result["models"][model] = {
        "goals": [{"moment_id": m["id"], **e} for m, e in zip(moments, entries)], "groups": [], "empty_goals": sum(1 for e in entries if not e["goal"]),
        "seconds_median": summary["seconds_median"], "seconds_max": summary["seconds_max"], "metrics": summary, "vision": True}
    json.dump(result, open(result_path, "w"), indent=1)
    json.dump(summary, open(os.path.join(goals_dir, "vision_metrics.json"), "w"), indent=1)
    conn.close()
    store.close()
    print("\nRESOURCE SUMMARY")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
