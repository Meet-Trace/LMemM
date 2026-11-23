import json
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
    return ts.store.db.execute("SELECT status, description FROM moment_descriptions ORDER BY recorded_at").fetchall()


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
            victim = ts.store.db.execute("SELECT observation_id FROM moment_member_index LIMIT 1").fetchone()[0]
            forget.forget(ts.store, {"observation_ids": [victim]})
            self.assertEqual(len(rows(ts)), 3)

    def test_descriptions_cannot_be_edited(self):
        with TempStore() as ts:
            build(ts)
            step(ts, FakeClient())
            for sql in ("UPDATE moment_descriptions SET description='x'", "DELETE FROM moment_descriptions"):
                with self.assertRaises(Exception):
                    ts.store.db.execute(sql)

    def test_gate_reads_macos_output(self):
        self.assertEqual(parse_battery("Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1) 12%; discharging")["percent"], 12)
        self.assertEqual(parse_memory_free("System-wide memory free percentage: 7%"), 7)
        reply = {"pmset": "Now drawing from 'Battery Power' 50%", "memory_pressure": "free percentage: 7%"}
        self.assertEqual(pause_reason(runner=lambda c: reply[c[0]]), "only 7% memory free")
        self.assertIsNone(pause_reason(runner=lambda c: ""))


if __name__ == "__main__":
    unittest.main()
