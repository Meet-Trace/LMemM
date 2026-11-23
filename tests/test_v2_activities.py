import unittest

import numpy as np

from test_v2_moments import CODE, MONEY, READ, live, run, sequence
from trace_v2.activities import PARAMS, run_pass
from trace_v2.moments.similarity import S
from trace_v2.observations import forget
from trace_v2.structure.processor import read_connection
from v2_observation_helpers import T0, TempStore

TOPICS = {"code": CODE, "money": MONEY, "read": READ}
HOUR = 3600
NOW = T0 + 40 * HOUR * S


class FakeInterpreter:
    model, prompt_version = "fake", "1"

    def __init__(self):
        self.interpreted, self.judged, self.fail_judge, self.verdicts = 0, 0, False, {}

    def topic(self, moment):
        tokens = set(moment["summary"]["top_tokens"])
        return max(TOPICS, key=lambda t: len(tokens & set(TOPICS[t])))

    def interpret(self, moment):
        self.interpreted += 1
        topic = self.topic(moment)
        return {"action": f"did {topic}", "purpose": f"finish the {topic} work", "why": "because", "clear": True, "topic": topic}

    def embed(self, texts):
        names = list(TOPICS)
        out = []
        for text in texts:
            v = np.zeros(4, dtype=np.float32)
            v[[i for i, n in enumerate(names) if n in text][0]] = 1.0
            out.append(v)
        return out

    def same(self, a, b):
        self.judged += 1
        if self.fail_judge:
            raise RuntimeError("model down")
        return self.verdicts.get("all", ("SAME" if a["purpose"] == b["purpose"] else "DIFFERENT", "same topic"))


def activity_rows(ts):
    rows = ts.store.derived.execute(
        "SELECT r.activity_id, r.state, r.moment_count, r.revision FROM activity_revisions r WHERE r.revision="
        "(SELECT max(x.revision) FROM activity_revisions x WHERE x.activity_id=r.activity_id) ORDER BY r.start_ns").fetchall()
    return [tuple(r[1:]) for r in rows]


def build(ts):
    sequence(ts.store, 0, 8, "com.apple.dt.Xcode", "shader.cpp", CODE, 0)
    sequence(ts.store, 3000, 8, "com.apple.Numbers", "budget.numbers", MONEY, 100)
    sequence(ts.store, 6000, 8, "com.apple.dt.Xcode", "mesh.cpp", CODE, 200)
    sequence(ts.store, 9000, 8, "com.apple.Preview", "paper.pdf", READ, 300)
    run(ts, NOW)
    assert len([m for m in live(ts) if m["state"] == "stable"]) == 4


def go(ts, interpreter, now=NOW, params=PARAMS, state=None):
    reader = read_connection(ts.store)
    try:
        return run_pass(ts.store, reader, interpreter, now, params, state)
    finally:
        reader.close()


class ActivityTest(unittest.TestCase):
    def test_same_goal_moments_join_and_others_stay_apart(self):
        with TempStore() as ts:
            build(ts)
            go(ts, FakeInterpreter())
            self.assertEqual(sorted(activity_rows(ts)), sorted([("active", 2, 2), ("forming", 1, 1), ("forming", 1, 1)]))

    def test_second_pass_does_no_new_work(self):
        with TempStore() as ts:
            build(ts)
            fake = FakeInterpreter()
            go(ts, fake)
            interpreted, judged = fake.interpreted, fake.judged
            out = go(ts, fake)
            self.assertEqual((fake.interpreted, fake.judged), (interpreted, judged))
            self.assertEqual((out["joined"], out["created"], out["interpreted"]), (0, 0, 0))

    def test_judge_must_agree_not_just_similarity(self):
        with TempStore() as ts:
            build(ts)
            fake = FakeInterpreter()
            fake.verdicts["all"] = ("DIFFERENT", "different outcomes")
            go(ts, fake)
            self.assertEqual({s for s, _, _ in activity_rows(ts)}, {"forming"})
            self.assertEqual(len(activity_rows(ts)), 4)

    def test_failed_judge_defers_instead_of_creating_a_wrong_activity(self):
        with TempStore() as ts:
            build(ts)
            fake = FakeInterpreter()
            go(ts, fake)
            sequence(ts.store, 12000, 8, "com.apple.dt.Xcode", "other.cpp", CODE, 400)
            run(ts, NOW + 4 * HOUR * S)
            fake.fail_judge = True
            out = go(ts, fake, NOW + 4 * HOUR * S)
            self.assertEqual(out["deferred"], 1)
            self.assertEqual(len(activity_rows(ts)), 3)
            fake.fail_judge = False
            go(ts, fake, NOW + 4 * HOUR * S)
            self.assertEqual(sorted(r[1] for r in activity_rows(ts)), [1, 1, 3])

    def test_idle_activity_is_archived(self):
        with TempStore() as ts:
            build(ts)
            fake = FakeInterpreter()
            go(ts, fake)
            go(ts, fake, NOW + 9 * 86400 * S)
            self.assertEqual({s for s, _, _ in activity_rows(ts)}, {"archived"})

    def test_forget_removes_what_was_learned_from_the_forgotten_screen(self):
        with TempStore() as ts:
            build(ts)
            go(ts, FakeInterpreter())
            victim = ts.store.derived.execute(
                "SELECT i.observation_id FROM moment_member_index i JOIN activity_member_index a ON a.moment_id=i.moment_id "
                "WHERE a.activity_id=(SELECT activity_id FROM activity_revisions WHERE moment_count=2 LIMIT 1) LIMIT 1").fetchone()[0]
            forget.forget(ts.store, {"observation_ids": [victim]})
            db = ts.store.derived
            self.assertEqual(db.execute("SELECT count(*) FROM activity_interpretations").fetchone()[0], 3)
            self.assertEqual(sorted(r[1] for r in activity_rows(ts)), [1, 1])
            go(ts, FakeInterpreter())
            self.assertEqual(sorted(r[1] for r in activity_rows(ts)), [1, 1, 1])

    def test_activities_cannot_be_edited(self):
        with TempStore() as ts:
            build(ts)
            go(ts, FakeInterpreter())
            for sql in ("UPDATE activity_revisions SET state='archived'", "DELETE FROM activities",
                        "DELETE FROM activity_interpretations", "DELETE FROM activity_verdicts"):
                with self.assertRaises(Exception):
                    ts.store.derived.execute(sql)


if __name__ == "__main__":
    unittest.main()
