"""Work History reads the Moments a real V2 store produced (v2_history.py against trace_v2's own tables)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import v2_history
from test_v2_activities import FakeInterpreter, build, go
from v2_observation_helpers import TempStore


class RealStoreTest(unittest.TestCase):
    def test_interpreted_moments_become_events_and_activity_members_share_an_index(self):
        with TempStore() as ts:
            build(ts)
            go(ts, FakeInterpreter())
            v2_history._CACHE.clear()
            events = v2_history.read(str(ts.store.root))
            self.assertEqual(len(events), 4)
            self.assertEqual(sorted(e["v2"]["title"] for e in events), ["finish the code work"] * 2 + ["finish the money work", "finish the read work"])
            groups = [e["v2"]["activity"] for e in events if e["v2"]["title"] == "finish the code work"]
            self.assertIsNotNone(groups[0])
            self.assertEqual(groups[0], groups[1])
            self.assertEqual([e["v2"]["activity"] for e in events if "code" not in e["v2"]["title"]], [None, None])
            self.assertTrue(all(e["v2"]["app"] for e in events))
            self.assertEqual([e["start"] for e in events], sorted(e["start"] for e in events))

    def test_a_forgotten_moment_leaves_history(self):
        from trace_v2.observations import forget
        with TempStore() as ts:
            build(ts)
            go(ts, FakeInterpreter())
            v2_history._CACHE.clear()
            before = v2_history.read(str(ts.store.root))
            oid = ts.store.derived.execute("SELECT observation_id FROM moment_member_index LIMIT 1").fetchone()[0]
            forget.forget(ts.store, {"observation_ids": [oid]}, dry_run=False)
            v2_history._CACHE.clear()
            after = v2_history.read(str(ts.store.root))
            self.assertLess(len(after or []), len(before))


if __name__ == "__main__":
    unittest.main()
