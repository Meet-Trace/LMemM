"""The annotation tool (src/activity_annotation.py). Synthetic data here tests the CODE; it is never a result."""
import hashlib
import json
import os
import stat
import tempfile
import unittest

import activity_annotation as aa
import activity_evaluation as ae
import activity_fixtures as fx


def write_dataset(d, records):
    os.makedirs(os.path.join(d, "moments"))
    with open(os.path.join(d, "moments", "2027-01-15.jsonl"), "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ds = os.path.join(self.tmp.name, "day")
        self.fixture = fx.long_gaps()
        write_dataset(self.ds, self.fixture.records)
        self.recs = aa.read_records(self.ds)
        self.p = aa.propose(self.recs)

    def block_of(self, visit):
        return next(b["id"] for b in self.p["blocks"] if visit in b["visits"])


class TestProposal(Base):
    def test_every_visit_in_exactly_one_card(self):
        seen = [v for b in self.p["blocks"] for v in b["visits"]]
        self.assertEqual(sorted(seen), sorted(self.p["visit_order"]))
        self.assertEqual(len(seen), len(set(seen)))

    def test_deterministic(self):
        self.assertEqual(json.dumps(self.p, sort_keys=True), json.dumps(aa.propose(self.recs), sort_keys=True))

    def test_return_after_long_gap_is_not_grouped_with_first_visit(self):
        # A1/A4: the same editor item is visited again an hour later: separate cards, offered only as a suggestion
        by_item = {}
        for v in self.p["visit_order"]:
            by_item.setdefault(self.p["visits"][v]["item"], []).append(v)
        ed = max(by_item.values(), key=len)
        self.assertGreaterEqual(len(ed), 2)
        self.assertNotEqual(self.block_of(ed[0]), self.block_of(ed[-1]))

    def test_no_card_spans_more_than_the_cap_through_links(self):
        o = self.p["options"]
        for b in self.p["blocks"]:
            if b["kind"] == "group" and len(b["stretches"]) > 1:
                self.assertLessEqual(b["t1"] - b["t0"], o["group_span_s"] + 1e-6)

    def test_same_item_proximity_alone_never_groups_stretches(self):
        # A3: a card with several stretches must have strong evidence; same-window links appear only as suggestions
        d = os.path.join(self.tmp.name, "day2")
        write_dataset(d, fx.same_tab_different_file().records)
        p = aa.propose(aa.read_records(d))
        self.assertEqual(sorted(v for b in p["blocks"] for v in b["visits"]), sorted(p["visit_order"]))
        for b in p["blocks"]:
            if len(b["stretches"]) > 1:
                items = {p["visits"][v]["item"] for v in b["visits"]}
                same_item_only = len(items) == 1
                self.assertFalse(same_item_only, "stretches of one item were grouped without evidence")


class TestEvents(Base):
    def setUp(self):
        super().setUp()
        self.ann = os.path.join(self.tmp.name, "ann")
        self.p, created = aa.init_folder(self.ann, self.ds)
        self.assertTrue(created)

    def ids(self):
        return [b["id"] for b in self.p["blocks"]]

    def test_label_merge_split_undo(self):
        ids = self.ids()
        st = aa.append_event(self.ann, self.p, {"op": "label", "blocks": [ids[0]], "status": "activity", "activity": "billing", "project": "Acme"})
        self.assertEqual(st["blocks"][ids[0]]["activity"], "billing")
        st = aa.append_event(self.ann, self.p, {"op": "merge", "blocks": [ids[0], ids[-1]]})
        merged = [b for b in st["blocks"].values() if b["origin"] == "merge"][0]
        self.assertEqual(merged["status"], "open")                      # labels disagreed: nothing is guessed
        self.assertEqual(len(merged["visits"]), len(self.p["blocks"][0]["visits"]) + len(self.p["blocks"][-1]["visits"]))
        st = aa.append_event(self.ann, self.p, {"op": "undo"})
        self.assertIn(ids[-1], st["blocks"])
        # split one visit out of a multi-visit card
        big = max(st["blocks"].values(), key=lambda b: len(b["visits"]))
        if len(big["visits"]) > 1:
            st = aa.append_event(self.ann, self.p, {"op": "split", "block": big["id"], "visits": big["visits"][-1:]})
            self.assertEqual(sum(len(b["visits"]) for b in st["blocks"].values()), self.p["n_visits"])

    def test_invalid_events_are_rejected_and_not_logged(self):
        n = len(aa.read_events(self.ann))
        for bad in ({"op": "label", "blocks": ["nope"], "status": "unrelated"},
                    {"op": "label", "blocks": [self.ids()[0]], "status": "activity", "activity": "-"},
                    {"op": "merge", "blocks": [self.ids()[0]]},
                    {"op": "undo"}, {"op": "bogus"}):
            with self.assertRaises(aa.AnnotationError):
                aa.append_event(self.ann, self.p, bad)
        self.assertEqual(len(aa.read_events(self.ann)), n)

    def test_log_is_append_only_and_resumable(self):
        aa.append_event(self.ann, self.p, {"op": "label", "blocks": [self.ids()[0]], "status": "unrelated"})
        before = open(aa.events_path(self.ann), "rb").read()
        aa.append_event(self.ann, self.p, {"op": "label", "blocks": [self.ids()[1]], "status": "uncertain"})
        self.assertTrue(open(aa.events_path(self.ann), "rb").read().startswith(before))
        p2, created = aa.init_folder(self.ann, self.ds)
        self.assertFalse(created)
        st = aa.replay(p2, aa.read_events(self.ann))
        self.assertEqual(st["blocks"][self.ids()[1]]["status"], "uncertain")

    def test_other_dataset_is_refused(self):
        d = os.path.join(self.tmp.name, "other")
        write_dataset(d, fx.continuous_multiapp().records)
        with self.assertRaises(aa.AnnotationError):
            aa.init_folder(self.ann, d)


class TestExport(Base):
    def test_export_feeds_the_existing_replay_and_leaves_originals_alone(self):
        ann = os.path.join(self.tmp.name, "ann")
        p, _ = aa.init_folder(ann, self.ds)
        src = os.path.join(self.ds, "moments", "2027-01-15.jsonl")
        before = sha(src)
        ids = [b["id"] for b in p["blocks"]]
        aa.append_event(ann, p, {"op": "label", "blocks": ids[:-2], "status": "activity", "activity": "billing", "project": "Acme"})
        aa.append_event(ann, p, {"op": "label", "blocks": [ids[-2]], "status": "unrelated"})
        aa.append_event(ann, p, {"op": "label", "blocks": [ids[-1]], "status": "uncertain"})
        r = aa.export(ann, self.ds)
        self.assertEqual(sha(src), before)
        self.assertEqual(sha(os.path.join(r["dataset"], "moments", "2027-01-15.jsonl")), before)
        ds = ae.load_dataset(r["dataset"])
        self.assertEqual(ds.problems, ())
        labels = [tuple(sorted(g)) for g in ds.gold.values()]
        self.assertIn(("billing",), labels)
        self.assertIn((), labels)                                       # unrelated is noise
        self.assertEqual(len(ds.gold) + r["moments_by_status"].get("uncertain", 0), len(self.recs))   # uncertain left blank
        self.assertNotIn("Acme", open(os.path.join(r["dataset"], "units.csv"), encoding="utf-8").read())   # Project never scored
        doc = json.load(open(os.path.join(ann, "activities.json")))
        self.assertEqual(doc["activities"]["billing"]["projects"], ["Acme"])
        prov = json.load(open(os.path.join(ann, "provenance.json")))
        self.assertFalse(prov["uses_inference_output"])
        self.assertEqual(prov["dataset_fingerprint"], aa.fingerprint(self.recs))
        mode = stat.S_IMODE(os.stat(os.path.join(ann, "provenance.json")).st_mode)
        self.assertEqual(mode, 0o600)

    def test_same_name_on_distant_cards_is_one_activity(self):
        ann = os.path.join(self.tmp.name, "ann")
        p, _ = aa.init_folder(ann, self.ds)
        ids = [b["id"] for b in p["blocks"]]
        aa.append_event(ann, p, {"op": "label", "blocks": [ids[0]], "status": "activity", "activity": "x"})
        aa.append_event(ann, p, {"op": "label", "blocks": [ids[-1]], "status": "activity", "activity": "x"})
        acts = aa.human_activities(aa.replay(p, aa.read_events(ann)), p)
        self.assertEqual(list(acts), ["x"])
        self.assertEqual(len(acts["x"]["cards"]), 2)


if __name__ == "__main__":
    unittest.main()
