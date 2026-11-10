"""Reading Moments for a replay: duplicates, conflicts, ordering, schema, and visit integrity V1 to V5. Pure Python."""
import copy
import json
import os
import tempfile
import unittest

import activity_fixtures as fx
import activity_harness as hn
import activity_input as ai

SENTINEL = "ZXQ-SENTINEL-SCREEN-TEXT"


def recs(name="continuous_multiapp"):
    return copy.deepcopy(fx.BUILDERS[name]().records)


def poison(records):
    """Put distinctive text into every content field, so a leak into an error message is visible."""
    for r in records:
        r["window"] = r["target"] = r["url"] = SENTINEL
        r["digest"]["terms"] = [SENTINEL.lower().replace("-", "")]
        r["digest"]["lines"] = [SENTINEL]
    return records


class LoadTests(unittest.TestCase):
    def test_canonical_order_and_grouping(self):
        r = recs()
        a = ai.prepare(r)
        b = ai.prepare(hn.shuffled(r, 1))
        self.assertEqual(a.records, b.records)
        keys = [ai.sort_key(x) for x in a.records]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(a.report["visits"], 12)
        self.assertEqual([v.visit_id for v in a.visits], [v.visit_id for v in b.visits])

    def test_identical_duplicates_dropped_and_counted(self):
        r = recs()
        dup = copy.deepcopy(r[0])
        dup["t_start"] = int(dup["t_start"])            # 1800000000.0 and 1800000000 are the same number
        p = ai.prepare(r + [dup, copy.deepcopy(r[1])])
        self.assertEqual(p.report["duplicates_dropped"], 2)
        self.assertEqual(len(p.records), len(r))

    def test_conflicting_duplicate_fails_loudly_without_content(self):
        r = poison(recs())
        other = copy.deepcopy(r[0])
        other["window"] = "something else"
        with self.assertRaises(ai.ConflictingMomentError) as cm:
            ai.prepare(r + [other])
        text = str(cm.exception)
        self.assertIn(r[0]["id"], text)
        self.assertIn("window", text)
        self.assertNotIn(SENTINEL, text)
        self.assertNotIn("something else", text)
        self.assertEqual(cm.exception.exit_code, 3)

    def test_unknown_schema_and_unknown_fields_fail(self):
        r = recs()
        r[0]["schema"] = 2
        with self.assertRaises(ai.UnsupportedRecordError) as cm:
            ai.prepare(r)
        self.assertIn("SCHEMA_UNSUPPORTED", str(cm.exception))
        for bad in ({"typed_text": "x"}, {"clipboard": "x"}):
            r = recs()
            r[2].update(bad)
            with self.assertRaises(ai.UnsupportedRecordError) as cm:
                ai.prepare(r)
            self.assertIn(list(bad)[0], str(cm.exception))
            self.assertNotIn("\"x\"", str(cm.exception))
        r = recs()
        r[0]["digest"]["typed"] = ["hello"]
        with self.assertRaises(ai.UnsupportedRecordError) as cm:
            ai.prepare(r)
        self.assertIn("digest.typed", str(cm.exception))

    def test_missing_fields_fail(self):
        r = recs()
        del r[0]["visit_id"]
        with self.assertRaises(ai.UnsupportedRecordError):
            ai.prepare(r)

    def test_malformed_lines_skipped_and_counted_or_strict(self):
        lines = [json.dumps(x) for x in recs()[:3]] + ['{"id": "half', "", "not json"]
        rep = ai.new_report()
        got = ai.parse_lines(lines, "d.jsonl", report=rep)
        self.assertEqual((len(got), rep["malformed_lines"]), (3, 2))
        with self.assertRaises(ai.MalformedLineError) as cm:
            ai.parse_lines(lines, "/x/d.jsonl", strict=True)
        self.assertIn("line=4", str(cm.exception))
        self.assertNotIn("half", str(cm.exception))

    def test_folder_roundtrip_is_read_only(self):
        f = fx.continuous_multiapp()
        with tempfile.TemporaryDirectory() as d:
            f.write_jsonl(d)
            before = sorted(os.listdir(d))
            p = ai.load(d)
            self.assertEqual(sorted(os.listdir(d)), before)
            self.assertEqual(p.records, ai.prepare(f.records).records)
            self.assertEqual(p.report["files"], len(before))

    def test_retention_window_relative_to_newest_record(self):
        r = recs()
        old = copy.deepcopy(r[0])
        old.update(id="v1-1.0", visit_id="v1-1", t_start=r[0]["t_start"] - 49 * 3600, t_end=r[0]["t_start"] - 49 * 3600 + 5)
        old["context"]["same_item_before"] = None
        p = ai.prepare(r + [old])
        self.assertEqual(p.report["outside_window"], 1)
        self.assertNotIn("v1-1.0", [x["id"] for x in p.records])


def retimed_ids(records, newid):
    """Rename one visit id everywhere it appears (id, visit_id, same_item_before, prev_moment_id)."""
    out = json.loads(json.dumps(records).replace(newid[0], newid[1]))
    return out


class VisitIdClockTests(unittest.TestCase):
    """V4: an id is an immutable label written at capture time; it is checked against its own history, never against
    time that a test has moved on purpose."""

    def setUp(self):
        self.recs = recs("continuous_multiapp")

    def test_capture_data_passes_with_full_checks(self):
        ai.prepare(self.recs)

    def test_translated_and_scaled_data_is_valid_without_the_capture_clock_part(self):
        for out in (hn.translate(self.recs, 1234.5), hn.translate(self.recs, -9.0e6), hn.scale(self.recs, 8), hn.scale(self.recs, 0.5)):
            p = ai.prepare(out, check_capture_clock=False, max_intra_visit_gap_s=2400)
            self.assertEqual(len(p.visits), 12)
            self.assertEqual([v.visit_id for v in p.visits], [v.visit_id for v in ai.prepare(self.recs).visits])   # ids untouched

    def test_the_same_translated_data_is_refused_by_the_capture_clock_part(self):
        with self.assertRaises(ai.VisitIntegrityError) as cm:
            ai.prepare(hn.translate(self.recs, 1234.5))
        self.assertTrue(all(p.startswith("V4") for p in cm.exception.problems))

    def test_relative_rules_still_apply_to_retimed_data(self):
        moved = hn.translate(self.recs, 5000.0)
        ids = [r["visit_id"] for r in moved if r["seq"] == 0]
        a, b = ids[0], ids[1]
        swapped = json.loads(json.dumps(moved).replace(a, "TMPA").replace(b, a).replace("TMPA", b))
        with self.assertRaises(ai.VisitIntegrityError) as cm:
            ai.prepare(swapped, check_capture_clock=False)
        self.assertTrue(any("id_order_conflicts_with_start_order" in p for p in cm.exception.problems))
        zero = json.loads(json.dumps(moved).replace(a, a.rsplit("-", 1)[0] + "-0"))
        with self.assertRaises(ai.VisitIntegrityError) as cm:
            ai.prepare(zero, check_capture_clock=False)
        self.assertTrue(any("counter_below_one" in p for p in cm.exception.problems))

    def test_malformed_ids_are_refused_in_every_mode(self):
        first = self.recs[0]["visit_id"]
        bad = json.loads(json.dumps(self.recs).replace(first, "not-a-visit"))
        for mode in (True, False):
            with self.assertRaises(ai.VisitIntegrityError) as cm:
                ai.prepare(bad, check_capture_clock=mode)
            self.assertTrue(any("id_format" in p for p in cm.exception.problems))

    def test_absolute_part_equality_for_complete_visits_and_bound_for_truncated_ones(self):
        r = recs("internal_shift")
        vid = r[0]["visit_id"]
        sec = int(vid[1:].split("-")[0])
        early = json.loads(json.dumps(r).replace(vid, f"v{sec - 5}-{vid.rsplit('-', 1)[1]}"))
        with self.assertRaises(ai.VisitIntegrityError) as cm:                  # complete visit: an earlier second is also wrong
            ai.prepare(early)
        self.assertTrue(any("id_second_differs_from_first_moment" in p for p in cm.exception.problems))
        ai.prepare(early, check_capture_clock=False)                            # the relative rules alone accept it
        cut = [x for x in early if not x["id"].endswith(".0") or x["visit_id"] != f"v{sec - 5}-{vid.rsplit('-', 1)[1]}"]
        ai.prepare(cut)                                                         # first moment dropped: second <= int(t_start) holds
        late = json.loads(json.dumps(cut).replace(f"v{sec - 5}-", f"v{sec + 900}-"))
        with self.assertRaises(ai.VisitIntegrityError) as cm:
            ai.prepare(late)
        self.assertTrue(any("id_second_after_first_remaining_moment" in p for p in cm.exception.problems))

    def test_relative_v4_limitations(self):
        """What the relative check does not catch, stated as a test so the guarantee is explicit."""
        b = fx.Builder(3)
        item = fx.code("a.py")
        v1 = b.visit(item, b.part("budget", 0.3), {"x"}, gap=0.0)
        v2 = b.visit(fx.code("b.py"), b.part("budget", 0.3), {"x"}, gap=0.2)
        v3 = b.visit(fx.code("c.py"), b.part("budget", 0.3), {"x"}, gap=2.0)       # a later second
        records = b.records
        self.assertEqual(v1.split("-")[0], v2.split("-")[0])                         # v1 and v2 start in the same second
        self.assertNotEqual(v2.split("-")[0], v3.split("-")[0])
        ai.prepare(records)
        # 1. counters swapped between two visits that start in the same second: accepted, in both modes
        swapped = json.loads(json.dumps(records).replace(v1, "TMP").replace(v2, v1).replace("TMP", v2))
        for mode in (True, False):
            ai.prepare(swapped, check_capture_clock=mode)
        # 2. ids all shifted together by a constant: the relative check accepts, the absolute check refuses
        shifted = json.loads(json.dumps(records).replace(v1.split("-")[0], "v" + str(int(v1.split("-")[0][1:]) + 40)))
        shifted = json.loads(json.dumps(shifted).replace(v2.split("-")[0], "v" + str(int(v2.split("-")[0][1:]) + 40)))
        shifted = json.loads(json.dumps(shifted).replace(v3.split("-")[0], "v" + str(int(v3.split("-")[0][1:]) + 40)))
        ai.prepare(shifted, check_capture_clock=False)
        with self.assertRaises(ai.VisitIntegrityError):
            ai.prepare(shifted)
        # 3. ids exchanged across seconds ARE caught by the relative check alone
        across = json.loads(json.dumps(records).replace(v1, "TMP").replace(v3, v1).replace("TMP", v3))
        with self.assertRaises(ai.VisitIntegrityError) as cm:
            ai.prepare(across, check_capture_clock=False)
        self.assertTrue(any("id_order_conflicts_with_start_order" in p for p in cm.exception.problems))
        # 4. a visit whose first moment was dropped takes no part in the relative comparison
        r = recs("internal_shift")
        vid = r[0]["visit_id"]
        cut = [x for x in r if x["id"] != vid + ".0"]
        wrong = json.loads(json.dumps(cut).replace(vid, "v1-" + vid.rsplit("-", 1)[1]))
        ai.prepare(wrong, check_capture_clock=False)                                  # accepted when the absolute part is off
        # the absolute check is the primary protection when the first moment is present
        with self.assertRaises(ai.VisitIntegrityError):
            ai.prepare(json.loads(json.dumps(records).replace(v3, "v" + str(int(v3.split("-")[0][1:]) - 3) + "-" + v3.split("-")[1])))

    def test_activity_ids_and_member_mappings_survive_retiming(self):
        import activity_contract as ac
        import activity_stubs as st
        P = ac.ReplayParams()
        base = hn.run(st.sittings_infer, self.recs, P)
        for out, params in ((hn.translate(self.recs, 777.0), P), (hn.translate(self.recs, -9.0e6), P), (hn.scale(self.recs, 2), P.scaled(2))):
            other = hn.run(st.sittings_infer, out, params, capture_clock=False)
            self.assertEqual({a["id"]: a["members"] for a in other["activities"]}, {a["id"]: a["members"] for a in base["activities"]})


class VisitIntegrityTests(unittest.TestCase):
    def two_moment_visit(self):
        r = copy.deepcopy(fx.internal_shift().records)
        vid = r[0]["visit_id"]
        return r, vid, [x for x in r if x["visit_id"] == vid]

    def fail(self, records, code):
        with self.assertRaises(ai.VisitIntegrityError) as cm:
            ai.prepare(records)
        self.assertEqual(cm.exception.exit_code, 3)
        self.assertTrue(any(p.startswith(code) for p in cm.exception.problems), cm.exception.problems)
        return str(cm.exception)

    def test_v1_two_unrelated_groups_under_one_visit_id(self):
        a, b = recs("two_in_one_app")[:2]
        b = poison([copy.deepcopy(b)])[0]
        b.update(visit_id=a["visit_id"], id=a["visit_id"] + ".1", seq=1, t_start=a["t_end"], t_end=a["t_end"] + 5)
        b["context"]["prev_moment_id"] = a["id"]
        text = self.fail([a, b], "V1")
        self.assertIn(a["visit_id"], text)
        self.assertNotIn(SENTINEL, text)

    def test_v2_id_seq_and_holes(self):
        r, vid, ms = self.two_moment_visit()
        r2 = copy.deepcopy(r)
        [x for x in r2 if x["visit_id"] == vid][1]["id"] = vid + ".7"
        self.fail(r2, "V2")
        r3 = copy.deepcopy(r)
        for x in r3:
            if x["id"] == vid + ".1":
                x["seq"], x["id"] = 3, vid + ".3"                       # a hole at 1 and 2
        self.fail(r3, "V2")

    def test_v2_prefix_and_suffix_truncation_are_fine(self):
        r = recs("internal_shift")
        vid = r[0]["visit_id"]
        no_first = [x for x in r if x["id"] != vid + ".0"]              # retention dropped the oldest moment
        p = ai.prepare(no_first)
        self.assertIn(vid, p.report["truncated_visits"])
        no_last = [x for x in r if x["id"] != vid + ".1"]               # a partial forget dropped the newest
        ai.prepare(no_last)

    def test_v3_overlap_negative_and_gap(self):
        r, vid, ms = self.two_moment_visit()
        r2 = copy.deepcopy(r)
        [x for x in r2 if x["id"] == vid + ".1"][0]["t_start"] -= 50
        self.fail(r2, "V3")
        r3 = copy.deepcopy(r)
        [x for x in r3 if x["id"] == vid + ".0"][0]["t_end"] = [x for x in r3 if x["id"] == vid + ".0"][0]["t_start"] - 10
        self.fail(r3, "V3")
        r4 = copy.deepcopy(r)
        for x in r4:
            if x["id"] == vid + ".1":
                x["t_start"] += 1000
                x["t_end"] += 1000
        self.fail(r4, "V3")
        ai.prepare(r4, max_intra_visit_gap_s=2000)                      # the bound is a parameter

    def test_v4_visit_id_second_later_than_its_moments(self):
        r = recs("same_pr_three_pages")
        old, new = r[0]["visit_id"], "v" + str(int(r[0]["t_start"]) + 500) + "-1"
        for x in r:
            if x["visit_id"] == old:
                x["visit_id"], x["id"] = new, new + "." + str(x["seq"])
        self.fail(r, "V4")

    def test_v5_context_chain(self):
        r, vid, ms = self.two_moment_visit()
        r2 = copy.deepcopy(r)
        [x for x in r2 if x["id"] == vid + ".1"][0]["context"]["prev_moment_id"] = "elsewhere.0"
        self.fail(r2, "V5")
        r3 = recs("long_gaps")                                         # the editor is visited twice
        later = [x for x in r3 if x["context"].get("same_item_before")][0]
        later["context"]["same_item_before"] = dict(later["context"]["same_item_before"], visit_id=later["visit_id"])
        self.fail(r3, "V5")

    def test_all_problems_are_listed_and_no_content_leaks(self):
        r = poison(recs())
        r[1]["item_id"] = "other"
        r[5]["ref"] = "other"
        text = self.fail(r, "V1") if False else None
        # single-moment visits cannot differ internally, so corrupt two visits by id mismatch instead
        r = poison(recs())
        r[0]["id"] = r[0]["visit_id"] + ".9"
        r[3]["id"] = r[3]["visit_id"] + ".9"
        with self.assertRaises(ai.VisitIntegrityError) as cm:
            ai.prepare(r)
        self.assertEqual(len(cm.exception.problems), 2)
        self.assertNotIn(SENTINEL, str(cm.exception))

    def test_failure_writes_nothing(self):
        r = recs()
        r[0]["id"] = "bad.id"
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "2027-01-01.jsonl"), "w") as fh:
                fh.write("\n".join(json.dumps(x) for x in r))
            before = sorted(os.listdir(d))
            with self.assertRaises(ai.VisitIntegrityError):
                ai.load(d)
            self.assertEqual(sorted(os.listdir(d)), before)

    def test_every_fixture_is_valid_input(self):
        for f in fx.all_fixtures():
            with self.subTest(f.name):
                ai.prepare(f.records)


if __name__ == "__main__":
    unittest.main()
