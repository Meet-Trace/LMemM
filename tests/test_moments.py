"""Moments: visits, the buffer rules, the 48 h store, and that forgetting deletes them. Pure Python (no macOS)."""
import json
import os
import stat
import tempfile
import unittest

import config
import forget
import moment_store
import moments
from moments import MomentBuffer, clean_url, digest_of

T0 = 1_800_000_000.0
DOC = "Wagtail pricing memo\nTake rate is fifteen percent on every booking\nReview pricing_model.xlsx with Tara"
OTHER = "Quarterly invoice summary\nOutstanding balance for Harbour Cafe\nSend reminder email Friday"


def feed(buf, t, item, text=DOC, cat="reading", dt=0.0, app="Docs", **kw):
    return buf.observe(t, item, app=app, window=item, category=cat, text=text, dt=dt, **kw)


class DigestTests(unittest.TestCase):
    def test_small_and_deterministic(self):
        a, b = digest_of(DOC), digest_of(DOC)
        self.assertEqual(a, b)
        self.assertIn("pricing_model.xlsx", a["identifiers"])
        self.assertLessEqual(len(a["lines"]), 3)
        self.assertTrue(all(len(l) <= 80 for l in a["lines"]))

    def test_secrets_are_dropped(self):
        d = digest_of("token sk-abcdefghijklmnopqrstuvwxyz0123456789 here\npassword: hunter2hunter2\nA normal sentence about pricing")
        blob = json.dumps(d)
        self.assertNotIn("sk-abcdef", blob)
        self.assertNotIn("hunter2", blob)
        self.assertIn("normal sentence", blob)

    def test_keep_lines_off(self):
        self.assertEqual(digest_of(DOC, keep_lines=False)["lines"], [])

    def test_url_loses_query_and_fragment(self):
        self.assertEqual(clean_url("https://Docs.Google.com/d/abc/edit?token=SECRET#heading=h.1"),
                         ("docs.google.com/d/abc/edit", "docs.google.com"))
        self.assertEqual(clean_url(None), (None, None))


class BufferTests(unittest.TestCase):
    def test_repaint_in_one_visit_is_one_moment(self):
        b = MomentBuffer()
        for i in range(6):
            feed(b, T0 + i * 5, "doc", dt=5 if i else 0)
        b.close()
        [m] = list(b.recent)
        self.assertEqual(m.n_frames, 6)
        self.assertEqual(m.seq, 0)
        self.assertEqual(m.dwell_s, 25)

    def test_flicker_is_not_a_change(self):
        b = MomentBuffer()
        feed(b, T0, "doc")
        self.assertEqual(feed(b, T0 + 5, "doc", text=DOC + "\nOK"), [])    # one tiny line: not material
        b.close()
        self.assertEqual(len(b.recent), 1)

    def test_material_text_change_starts_a_moment_in_the_same_visit(self):
        b = MomentBuffer()
        feed(b, T0, "doc")
        closed = feed(b, T0 + 5, "doc", text=DOC + "\nCompetitor benchmark shows subscription churn near eight percent")
        self.assertEqual(len(closed), 1)
        b.close()
        a, c = list(b.recent)
        self.assertEqual((a.visit_id, c.visit_id), (c.visit_id, a.visit_id))
        self.assertEqual((a.seq, c.seq), (0, 1))
        self.assertEqual(c.context["prev_moment_id"], a.id)

    def test_new_identifier_is_material(self):
        b = MomentBuffer()
        feed(b, T0, "doc")
        self.assertEqual(len(feed(b, T0 + 5, "doc", text=DOC + "\nsee budget_v2.xlsx")), 1)

    def test_category_change_needs_time_in_the_moment(self):
        b = MomentBuffer(min_category_s=10)
        feed(b, T0, "doc", cat="reading")
        self.assertEqual(feed(b, T0 + 4, "doc", cat="typing"), [])           # too soon
        self.assertEqual(len(feed(b, T0 + 12, "doc", cat="typing")), 1)      # 12 s in: a new moment
        self.assertEqual(feed(b, T0 + 20, "doc", cat="reading"), [])         # only 8 s into it

    def test_moment_is_capped(self):
        b = MomentBuffer(max_moment_s=60, visit_gap=1000)
        feed(b, T0, "doc")
        closed = feed(b, T0 + 70, "doc")
        self.assertEqual(len(closed), 1)
        self.assertTrue(b.open.continued)

    # M1: the constraints about visits

    def test_a_short_look_at_b_is_not_absorbed(self):
        b = MomentBuffer()
        feed(b, T0, "A", app="Docs")
        feed(b, T0 + 60, "B", text=OTHER, app="Mail")
        feed(b, T0 + 75, "A", app="Docs")                                   # 15 s at B, then back
        b.close()
        got = [(m.item_id, m.visit_id) for m in b.recent]
        self.assertEqual([i for i, _ in got], ["A", "B", "A"])
        self.assertEqual(len({v for _, v in got}), 3)

    def test_identical_content_in_two_visits_stays_two_visits(self):
        b = MomentBuffer()
        feed(b, T0, "doc")
        feed(b, T0 + 30, "other", text=OTHER)
        feed(b, T0 + 40, "doc")                                             # identical content, new visit
        b.close()
        first, _, last = list(b.recent)
        self.assertNotEqual(first.visit_id, last.visit_id)
        self.assertNotEqual(first.id, last.id)
        self.assertEqual(first.digest["text_hash"], last.digest["text_hash"])
        self.assertEqual(last.context["same_item_before"]["visit_id"], first.visit_id)
        self.assertEqual(last.context["same_item_before"]["visits_before"], 1)
        self.assertEqual(first.context["same_item_before"], None)

    def test_leaving_credits_the_time_up_to_the_switch(self):
        b = MomentBuffer()
        feed(b, T0, "A")
        feed(b, T0 + 40, "B", text=OTHER)
        self.assertEqual(b.recent[0].dwell_s, 40)

    def test_a_gap_splits_a_visit_even_on_the_same_item(self):
        b = MomentBuffer(visit_gap=90)
        feed(b, T0, "doc")
        feed(b, T0 + 30, "doc")
        feed(b, T0 + 30 + 600, "doc")                                       # came back after ten minutes
        b.close()
        a, c = list(b.recent)
        self.assertNotEqual(a.visit_id, c.visit_id)
        self.assertGreaterEqual(c.context["same_item_before"]["gap_s"], 570)
        self.assertGreaterEqual(c.context["gap_s"], 570)

    def test_visit_boundaries_are_all_kept(self):
        b = MomentBuffer()
        plan = ["A", "B", "A", "C", "A", "A", "B", "B", "A"]
        for i, item in enumerate(plan):
            feed(b, T0 + i * 20, item, text=OTHER if item != "A" else DOC)
        b.close()
        runs = 1 + sum(1 for x, y in zip(plan, plan[1:]) if x != y)
        self.assertEqual(len({m.visit_id for m in b.recent}), runs)

    def test_idle_flush_closes_after_the_gap(self):
        b = MomentBuffer(visit_gap=90)
        feed(b, T0, "doc")
        self.assertEqual(b.idle_flush(T0 + 30), [])
        self.assertEqual(len(b.idle_flush(T0 + 200)), 1)

    # buffer limits, no keys, sinks

    def test_window_and_cap(self):
        b = MomentBuffer(window_s=100, max_moments=3)
        for i in range(8):
            feed(b, T0 + i * 20, f"i{i}", text=f"Distinct words number{i} alphabetically unique{i}")
        self.assertLessEqual(len(b.recent), 3)

    def test_interaction_holds_counts_and_ages_never_keys(self):
        b = MomentBuffer()
        feed(b, T0, "doc", cat="typing", ages={"key": 1.2, "scroll": 1e9, "click": 7.0, "chars": "secret"})
        m = b.open
        self.assertEqual(m.interaction["ages"], {"key": 1.2, "scroll": None, "click": 7.0})
        self.assertNotIn("secret", json.dumps(m.to_dict()))

    def test_sink_failure_does_not_stop_the_buffer(self):
        def boom(_):
            raise OSError("disk full")
        b = MomentBuffer(sink=boom)
        feed(b, T0, "A")
        feed(b, T0 + 5, "B", text=OTHER)
        self.assertEqual(len(b.recent), 1)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "memory", "moments")
        self.store = moment_store.MomentStore(self.dir, retention_hours=48)
        self.buf = MomentBuffer(sink=self.store.append)

    def tearDown(self):
        self.tmp.cleanup()

    def fill(self, t, item="doc", app="Docs", text=DOC):
        feed(self.buf, t, item, text=text, app=app)
        self.buf.close()

    def test_round_trip_and_permissions(self):
        self.fill(T0)
        [r] = self.store.read()
        self.assertEqual(r["item_id"], "doc")
        m = moments.Moment.from_dict(r)
        self.assertEqual(m.id, r["id"])
        self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)
        [f] = os.listdir(self.dir)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dir, f)).st_mode), 0o600)

    def test_sweep_drops_only_old_records(self):
        self.fill(T0)
        self.fill(T0 + 49 * 3600, item="new", text=OTHER)
        self.assertEqual(self.store.sweep(now=T0 + 49 * 3600 + 10), 1)
        self.assertEqual([r["item_id"] for r in self.store.read()], ["new"])

    def test_forget_by_time_and_app(self):
        self.fill(T0, "a", "Docs")
        self.fill(T0 + 600, "b", "Mail", OTHER)
        self.assertEqual(self.store.forget(app="Mail"), 1)
        self.assertEqual([r["item_id"] for r in self.store.read()], ["a"])
        self.assertEqual(self.store.forget(minutes=30, now=T0 + 100), 1)
        self.assertEqual(self.store.read(), [])
        self.assertEqual(os.listdir(self.dir), [])

    def test_half_written_last_line_is_skipped(self):
        self.fill(T0)
        with open(os.path.join(self.dir, os.listdir(self.dir)[0]), "a") as fh:
            fh.write('{"id": "broken')
        self.assertEqual(len(self.store.read()), 1)

    def test_delete_all_removes_moments(self):
        data = os.path.join(self.tmp.name, "a", "b", "data")
        d = os.path.join(data, "memory", "moments")
        s = moment_store.MomentStore(d)
        buf = MomentBuffer(sink=s.append)
        feed(buf, T0, "doc")
        buf.close()
        self.assertEqual(len(s.read()), 1)
        forget.delete_all(data)
        self.assertEqual(s.read(), [])
        self.assertFalse(os.path.exists(d))


class RecorderTests(unittest.TestCase):
    def test_off_by_default(self):
        self.assertFalse(config.MOMENT_LOG)
        self.assertEqual(config.MOMENT_RETENTION_HOURS, 48)

    def test_frame_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            r = moment_store.Recorder(moment_store.MomentStore(os.path.join(d, "m")))
            r.frame(T0, "doc", {"inputs": {"key": 1.0}}, {"app": "Docs"}, "ref", {"category": "reading"}, 0, None, False)
            r.frame(T0, "doc", None, None, None, None, 0, None, False)       # garbage in: swallowed
            r.close()
            self.assertEqual(len(r.store.read()), 1)


if __name__ == "__main__":
    unittest.main()
