import json
import time
import unittest
from unittest import mock

from test_v2_activities import FakeInterpreter, NOW, build, go
from trace_v2.descriptions import PARAMS, PROMPT_VERSION, run_pass, vision
from trace_v2.descriptions.gate import parse_battery, parse_memory_free, pause_reason
from trace_v2.observations import forget
from trace_v2.structure.processor import read_connection
from v2_observation_helpers import TempStore


class FakeClient:
    def __init__(self, answer=None, fail=False):
        self.calls, self.fail, self.answer = [], fail, answer

    def chat(self, text, images):
        self.calls.append((text, len(images)))
        if self.fail:
            raise ConnectionError("down")
        if self.answer is not None:
            return self.answer
        return json.dumps({"moment_description": "Editing a shader file",
                           "evidence": [{"type": "content", "description": "code on screen", "observations": ["O1"]}],
                           "confidence": 0.8, "alternative_interpretation": None, "insufficient_evidence": False})


def step(ts, client, params=PARAMS, state=None, gate=None):
    reader = read_connection(ts.store)
    try:
        return run_pass(ts.store, reader, client, "vlm", params, state, gate)
    finally:
        reader.close()


def rows(ts):
    return ts.store.derived.execute("SELECT status, description FROM moment_descriptions ORDER BY recorded_at").fetchall()


class DescriptionTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(vision, "encode_image", lambda data, side: "img")
        patch.start()
        self.addCleanup(patch.stop)

    def test_each_stable_moment_is_described_once(self):
        with TempStore() as ts:
            build(ts)
            client = FakeClient()
            for _ in range(6):
                step(ts, client)
            self.assertEqual(len(client.calls), 4)
            self.assertEqual({r for r in rows(ts)}, {("ok", "Editing a shader file")})
            self.assertGreater(client.calls[0][1], 0)
            step(ts, client)
            self.assertEqual(len(client.calls), 4)

    def test_paused_machine_does_no_work(self):
        with TempStore() as ts:
            build(ts)
            client, state = FakeClient(), {}
            out = step(ts, client, state=state, gate=lambda: "battery at 10%")
            self.assertEqual((len(client.calls), out["paused"], state["paused"]), (0, 1, "battery at 10%"))

    def test_unreachable_model_is_retried_then_recorded_as_failed(self):
        with TempStore() as ts:
            build(ts)
            client, state = FakeClient(fail=True), {}
            for _ in range(3):
                step(ts, client, state=state)
            self.assertEqual([r[0] for r in rows(ts)], ["failed"])

    def test_description_without_traceable_evidence_is_not_trusted(self):
        with TempStore() as ts:
            build(ts)
            step(ts, FakeClient(json.dumps({"moment_description": "Doing something", "evidence": [], "confidence": 0.9,
                                            "insufficient_evidence": False})))
            self.assertEqual(rows(ts), [("insufficient", None)])

    def test_activities_use_the_description_and_wait_for_it(self):
        with TempStore() as ts:
            build(ts)
            fake = FakeInterpreter()
            fake.description_model = "vlm"
            go(ts, fake)
            self.assertEqual(fake.interpreted, 0)
            for _ in range(4):
                step(ts, FakeClient())
            seen = []
            real = fake.interpret
            fake.interpret = lambda m: (seen.append(m["description"]), real(m))[1]
            go(ts, fake)
            self.assertEqual(seen, ["Editing a shader file"] * 4)

    def test_forget_removes_descriptions(self):
        with TempStore() as ts:
            build(ts)
            for _ in range(4):
                step(ts, FakeClient())
            victim = ts.store.derived.execute("SELECT observation_id FROM moment_member_index LIMIT 1").fetchone()[0]
            forget.forget(ts.store, {"observation_ids": [victim]})
            self.assertEqual(len(rows(ts)), 3)

    def test_descriptions_cannot_be_edited(self):
        with TempStore() as ts:
            build(ts)
            step(ts, FakeClient())
            for sql in ("UPDATE moment_descriptions SET description='x'", "DELETE FROM moment_descriptions"):
                with self.assertRaises(Exception):
                    ts.store.derived.execute(sql)

    def test_gate_reads_macos_output(self):
        self.assertEqual(parse_battery("Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1) 12%; discharging")["percent"], 12)
        self.assertEqual(parse_memory_free("System-wide memory free percentage: 7%"), 7)
        reply = {"pmset": "Now drawing from 'Battery Power' 50%", "memory_pressure": "free percentage: 7%"}
        self.assertEqual(pause_reason(runner=lambda c: reply.get(c[0], "")), "only 7% memory free")
        self.assertIsNone(pause_reason(runner=lambda c: ""))


if __name__ == "__main__":
    unittest.main()


class SegmentTest(unittest.TestCase):
    def test_cuts_fall_where_the_screen_changed(self):
        from test_v2_moments import CODE, MONEY, sequence
        from trace_v2.descriptions import read
        with TempStore() as ts:
            ids = sequence(ts.store, 0, 20, "com.apple.Xcode", "shader.cpp", CODE, 0)
            ids += sequence(ts.store, 70, 3, "com.apple.Numbers", "budget", MONEY, 100)
            ids += sequence(ts.store, 90, 17, "com.apple.Xcode", "shader.cpp", CODE, 200)
            reader = read_connection(ts.store)
            spans = read.segments(reader, ids, 8)
            reader.close()
            self.assertEqual(len(spans), 8)
            self.assertEqual(spans[0][0], 0)
            self.assertEqual(spans[-1][1], len(ids))
            starts = {lo for lo, _ in spans}
            self.assertIn(20, starts)
            self.assertIn(23, starts)


class TransparentCropTest(unittest.TestCase):
    def png(self, mode, box):
        import io
        from PIL import Image
        image = Image.new(mode, (400, 300), (0, 0, 0, 0) if mode == "RGBA" else (255, 255, 255))
        if mode == "RGBA":
            image.paste((200, 30, 30, 255), box)
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue()

    def decode(self, text):
        import base64, io
        from PIL import Image
        return Image.open(io.BytesIO(base64.b64decode(text)))

    def test_a_front_window_capture_is_cropped_to_the_window(self):
        out = self.decode(vision.encode_image(self.png("RGBA", (100, 50, 300, 250)), 896))
        self.assertEqual(out.size, (200, 200))

    def test_an_opaque_capture_is_unchanged(self):
        out = self.decode(vision.encode_image(self.png("RGB", None), 896))
        self.assertEqual(out.size, (400, 300))


class IdleGateTest(unittest.TestCase):
    IDLE = '| |   "HIDIdleTime" = %d'

    def test_model_work_waits_while_you_are_working(self):
        from trace_v2.descriptions.gate import parse_idle_s
        self.assertEqual(parse_idle_s(self.IDLE % 5_000_000_000), 5.0)
        self.assertIsNone(parse_idle_s("nothing here"))
        busy = lambda c: self.IDLE % 5_000_000_000 if c[0] == "ioreg" else ""
        away = lambda c: self.IDLE % 90_000_000_000 if c[0] == "ioreg" else ""
        self.assertIn("you are working", pause_reason(runner=busy))
        self.assertIsNone(pause_reason(runner=away))
        self.assertIsNone(pause_reason(min_idle_s=0, runner=busy))

    def test_a_changing_screen_counts_as_working_even_without_input(self):
        from trace_v2.descriptions.gate import user_active
        away = lambda c: self.IDLE % 90_000_000_000 if c[0] == "ioreg" else ""
        self.assertIn("screen is changing", pause_reason(runner=away, screen_idle=lambda: 5.0))
        self.assertIsNone(pause_reason(runner=away, screen_idle=lambda: 200.0))
        self.assertIsNone(pause_reason(runner=away, screen_idle=lambda: None))
        self.assertTrue(user_active(runner=away, screen_idle=lambda: 1.0))
        self.assertFalse(user_active(runner=away, screen_idle=lambda: 30.0))

    def test_the_trigger_ignores_heartbeats_when_timing_screen_idleness(self):
        from trace_v2.observations.trigger import CaptureTrigger
        from types import SimpleNamespace as NS
        context = NS(application={}, window={}, display={})
        t = CaptureTrigger()
        self.assertIsNone(t.screen_idle_s(10 * 10**9))
        t._remember(0, context, None, "screen")
        t._remember(40 * 10**9, context, None, "heartbeat")
        self.assertEqual(t.screen_idle_s(50 * 10**9), 50.0)

    def test_the_activity_processor_does_nothing_while_the_gate_says_wait(self):
        from test_v2_activities import FakeInterpreter, build
        from trace_v2.activities.processor import ActivityProcessor
        with TempStore() as ts:
            build(ts)
            fake = FakeInterpreter()
            proc = ActivityProcessor(ts.store, fake, gate=lambda: "you are working", clock_ns=lambda: build.__globals__["NOW"])
            self.assertEqual(sum(proc.run_once().values()), 0)
            self.assertEqual(fake.interpreted, 0)
            self.assertEqual(proc.stats()["paused"], "you are working")
            proc.gate = lambda: None
            self.assertGreater(proc.run_once()["interpreted"], 0)

    def test_an_interrupted_activity_call_stops_the_loop_cleanly_and_counts_nothing(self):
        from test_v2_activities import FakeInterpreter, build
        from trace_v2.activities.processor import ActivityProcessor
        from trace_v2.descriptions.call import Interrupted

        class Back(FakeInterpreter):
            def interpret(self, moment):
                raise Interrupted()

        with TempStore() as ts:
            build(ts)
            proc = ActivityProcessor(ts.store, Back(), gate=lambda: None, clock_ns=lambda: build.__globals__["NOW"], interval_s=0.05)
            proc.start()
            time.sleep(0.5)
            proc.stop()
            self.assertEqual(proc.stats()["paused"], "you are back")
            self.assertFalse(proc._state.get("attempts"))

    def test_embeddings_skip_the_cancel_check(self):
        from trace_v2.activities import ollama
        seen = []
        with mock.patch.object(ollama, "post_json", lambda url, body, timeout, abort: seen.append(abort) or {"embeddings": [[1.0, 0.0]]}):
            ollama.OllamaInterpreter("m", abort_when=lambda: True).embed(["a"])
        self.assertEqual(seen, [None])


class InterruptedCallTest(unittest.TestCase):
    def serve(self, delay):
        import http.server, threading, time as _t

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                _t.sleep(delay)
                try:
                    body = b'{"message": {"content": "ok"}}'
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}/api/chat"

    def test_a_call_is_cut_when_the_user_comes_back(self):
        import time as _t
        from trace_v2.descriptions.call import Interrupted, post_json
        url = self.serve(5)
        started = _t.perf_counter()
        with self.assertRaises(Interrupted):
            post_json(url, {}, 30, abort_when=lambda: True, poll_s=0.1)
        self.assertLess(_t.perf_counter() - started, 3)

    def test_a_call_that_is_left_alone_returns_its_answer(self):
        from trace_v2.descriptions.call import post_json
        self.assertEqual(post_json(self.serve(0.2), {}, 30, abort_when=lambda: False, poll_s=0.1)["message"]["content"], "ok")

    def test_an_interrupted_description_is_not_recorded_or_counted_as_a_failure(self):
        from trace_v2.descriptions.call import Interrupted

        class Client:
            def chat(self, text, images):
                raise Interrupted()

        with mock.patch.object(vision, "encode_image", lambda data, side: "img"), TempStore() as ts:
            build(ts)
            state = {}
            with self.assertRaises(Interrupted):
                step(ts, Client(), state=state)
            self.assertEqual(state.get("attempts", {}), {})
            self.assertEqual(ts.store.derived.execute("SELECT count(*) FROM moment_descriptions").fetchone()[0], 0)
