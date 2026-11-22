import io
import json
import os
import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "experiments", "moment_goals"))

import run_vision as V
from trace_v2.observations import ScreenshotCapture
from trace_v2.structure.processor import read_connection
from v2_observation_helpers import T0, TempStore, draft


def png_bytes(width=64, height=48):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (30, 60, 90)).save(buffer, "PNG")
    return buffer.getvalue()


class PureTests(unittest.TestCase):
    def test_tag_choice_is_even_and_ordered(self):
        tags = {f"O{i}": [f"id{i}"] for i in range(1, 11)}
        self.assertEqual(V.choose_tags(tags, 4), ["O1", "O4", "O7", "O10"])
        self.assertEqual(V.choose_tags(tags, 0), [f"O{i}" for i in range(1, 11)])
        self.assertEqual(V.choose_tags({"O1": ["a"], "O2": ["b"]}, 4), ["O1", "O2"])
        self.assertEqual(V.choose_tags(tags, 1), ["O6"])

    def test_representative_is_the_middle_member(self):
        self.assertEqual(V.representative(["a", "b", "c"]), "b")

    def test_message_names_the_attached_observations(self):
        message = V.build_message("PROMPT", ["O1", "O3"], ["img1", "img2"])
        self.assertEqual(message["role"], "user")
        self.assertEqual(message["images"], ["img1", "img2"])
        self.assertIn("observations O1, O3", message["content"])
        self.assertTrue(message["content"].endswith("PROMPT"))

    def test_ps_parsing_sums_only_ollama_processes(self):
        listing = "  204800 3.5 /usr/local/bin/ollama\n  102400 40.0 /Applications/Ollama.app/ollama runner\n  500000 10.0 /Applications/Safari\n"
        out = V.parse_ps(listing)
        self.assertEqual(out["processes"], 2)
        self.assertAlmostEqual(out["rss_mb"], 300.0)
        self.assertAlmostEqual(out["cpu_pct"], 43.5)

    def test_system_readings_are_parsed(self):
        self.assertEqual(V.parse_battery("Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1) 82%; discharging"), {"percent": 82, "source": "battery"})
        self.assertEqual(V.parse_battery("Now drawing from 'AC Power'\n 100%; charged")["source"], "ac")
        self.assertEqual(V.parse_swap("vm.swapusage: total = 2048.00M  used = 1130.25M  free = 917.75M"), 1130.25)
        self.assertEqual(V.parse_therm("CPU_Scheduler_Limit = 100\nCPU_Available_CPUs = 8\nCPU_Speed_Limit = 85"), 85)
        self.assertEqual(V.parse_memory_free("System-wide memory free percentage: 41%"), 41)
        self.assertIsNone(V.parse_battery("")["percent"])

    def test_summary_statistics(self):
        def entry(seconds, status="ok"):
            return {"seconds": seconds, "eval_count": 100, "prompt_eval_count": 900, "parsed": {"status": status},
                    "metrics": {"gen_tokens_per_s": 20.0, "rss_peak_mb": 900.0 + seconds, "ollama_cpu_avg_pct": 300.0, "system_cpu_avg_pct": 40.0,
                                "probe_ms": 50.0, "load_s": 1.0}}
        snap = {"battery": {"percent": 80, "source": "battery"}, "thermal_cpu_limit": 100, "swap_used_mb": 1000.0, "memory_free_pct": 40}
        end = {**snap, "battery": {"percent": 78, "source": "battery"}}
        out = V.summarize([entry(10), entry(20), entry(30, "insufficient")], 25.0, snap, end, [{"model": "m", "size_mb": 900, "vram_mb": 0}])
        self.assertEqual(out["calls"], 3)
        self.assertEqual(out["seconds_median"], 20)
        self.assertEqual(out["slowdown_factor"], 2.0)
        self.assertEqual(out["battery_percent_start_end"], (80, 78))
        self.assertFalse(out["ran_on_gpu"])
        self.assertEqual((out["parsed_ok"], out["insufficient"]), (2, 1))
        self.assertEqual(out["ollama_rss_peak_mb"], 930.0)

    def test_probe_returns_a_positive_duration(self):
        self.assertGreater(V.probe_ms(), 0)


class MomentCallTests(unittest.TestCase):
    ARGS = SimpleNamespace(max_images=2, image_side=32, num_ctx=2048, num_predict=100, threads=0, timeout=10, cpu=True, format="none", repeat_penalty=None, temperature=None, think="off")

    def test_each_moment_is_one_fresh_single_message_chat_with_screenshots(self):
        calls = []

        def fake_call(host, path, body, timeout=900):
            calls.append((path, body))
            return {"message": {"content": json.dumps({"moment_description": "Navigating the tracker", "evidence": [
                {"type": "interaction", "description": "scrolling", "observations": ["O1"]}], "confidence": 0.6,
                "alternative_interpretation": None, "insufficient_evidence": False})},
                "eval_count": 40, "prompt_eval_count": 700, "eval_duration": 2_000_000_000, "prompt_eval_duration": 3_000_000_000,
                "load_duration": 1_000_000_000}

        original = V.call
        V.call = fake_call
        try:
            with TempStore() as ts:
                ids = []
                for i in range(3):
                    t = T0 + i * 10 * 10 ** 9
                    shot = ScreenshotCapture(png_bytes(), "png", 64, 48, t)
                    ids.append(ts.store.ingest(replace(draft(key=f"k{i}", t=t), screenshot=shot)).observation_id)
                conn = read_connection(ts.store)
                try:
                    moment = {"id": "m1", "prompt": "THE PROMPT", "tags": {"O1": [ids[0]], "O2": [ids[1]], "O3": [ids[2]]}}
                    entry = V.one_moment(conn, str(ts.store.assets.root), "http://x", "lfm", moment, self.ARGS)
                finally:
                    conn.close()
        finally:
            V.call = original
        self.assertEqual(len(calls), 1)
        path, body = calls[0]
        self.assertEqual(path, "/api/chat")
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertEqual(len(body["messages"][0]["images"]), 2)
        self.assertEqual(body["options"]["num_gpu"], 0)
        self.assertNotIn("repeat_penalty", body["options"])
        self.assertNotIn("temperature", body["options"])
        self.assertNotIn("num_thread", body["options"])
        self.assertNotIn("format", body)
        self.assertIs(body["think"], False)
        self.assertIn("THE PROMPT", body["messages"][0]["content"])
        self.assertEqual(entry["parsed"]["status"], "ok")
        self.assertEqual(entry["goal"], "Navigating the tracker")
        self.assertEqual(entry["image_tags"], ["O1", "O3"])
        self.assertEqual(entry["metrics"]["gen_tokens_per_s"], 20.0)
        self.assertEqual(entry["metrics"]["image_count"], 2)

    def test_format_options_reach_the_request(self):
        seen = []
        original = V.call
        V.call = lambda host, path, body, timeout=900: seen.append(body) or {}
        try:
            for fmt in ("json", "schema"):
                V.chat("http://x", "m", {"role": "user", "content": "p"}, SimpleNamespace(**{**vars(self.ARGS), "format": fmt}))
        finally:
            V.call = original
        self.assertEqual(seen[0]["format"], "json")
        self.assertEqual(seen[1]["format"]["properties"]["evidence"]["maxItems"], 3)

    def test_reasoning_only_reply_is_explained(self):
        original = V.call
        V.call = lambda host, path, body, timeout=900: {"message": {"content": "", "thinking": "Let me look at the screenshots..."}, "eval_count": 450}
        try:
            with TempStore() as ts:
                t = T0
                ids = [ts.store.ingest(replace(draft(key="k", t=t), screenshot=ScreenshotCapture(png_bytes(), "png", 64, 48, t))).observation_id]
                conn = read_connection(ts.store)
                try:
                    entry = V.one_moment(conn, str(ts.store.assets.root), "http://x", "lfm", {"id": "m", "prompt": "P", "tags": {"O1": ids}}, self.ARGS)
                finally:
                    conn.close()
        finally:
            V.call = original
        self.assertEqual(entry["parsed"]["status"], "invalid")
        self.assertTrue(entry["raw"].startswith("[reasoning only]"))
        self.assertTrue(any("only reasoning" in e for e in entry["parsed"]["errors"]))

    def test_moment_without_stored_screenshots_is_reported_not_sent(self):
        with TempStore() as ts:
            conn = read_connection(ts.store)
            try:
                entry = V.one_moment(conn, str(ts.store.assets.root), "http://x", "lfm", {"id": "m", "prompt": "P", "tags": {"O1": ["missing"]}}, self.ARGS)
            finally:
                conn.close()
        self.assertEqual(entry["parsed"]["status"], "invalid")
        self.assertIn("no stored screenshots", entry["parsed"]["errors"][0])

    def test_request_failure_is_recorded(self):
        def broken(host, path, body, timeout=900):
            raise OSError("connection refused")
        original = V.call
        V.call = broken
        try:
            with TempStore() as ts:
                t = T0
                ids = [ts.store.ingest(replace(draft(key="k", t=t), screenshot=ScreenshotCapture(png_bytes(), "png", 64, 48, t))).observation_id]
                conn = read_connection(ts.store)
                try:
                    entry = V.one_moment(conn, str(ts.store.assets.root), "http://x", "lfm", {"id": "m", "prompt": "P", "tags": {"O1": ids}}, self.ARGS)
                finally:
                    conn.close()
        finally:
            V.call = original
        self.assertTrue(any("request failed" in e for e in entry["parsed"]["errors"]))


if __name__ == "__main__":
    unittest.main()
